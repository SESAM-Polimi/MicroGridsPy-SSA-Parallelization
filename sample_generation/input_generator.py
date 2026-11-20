"""Generate a country sample (advanced_sample.csv) from the source GIS inputs.

    python sample_generation/input_generator.py ZWE [KEN ...] --inputs /path/to/sample_inputs

Writes data/sample_input_2025/{ISO}/advanced_sample.csv (or --out-root) and, next to it,
advanced_sample.provenance.json with the size and SHA-256 of every input file used, the commit
and the date, so a sample can be traced back to the exact source files.

Source inputs (~3 GB, not in git): --inputs or $MGPY2_SAMPLE_INPUTS, the folder laid out as
the `input_generator/` folder of branch `input`:
    {inputs}/Input/{ISO}/{ISO}_Population_Clusters_EPSG4326.gpkg.zip   GHSL clusters (M-LED)
    {inputs}/Input/{ISO}/schools_{ISO}.gpkg                             schools
    {inputs}/Input/{ISO}/gadm41_{ISO}.gpkg                              GADM 4.1 boundaries
    {inputs}/Input/{ISO}/{iso}_pop_2025_CN_1km_R2025A_UA_v1.tif         WorldPop R2025A, 2025, 1 km
    {inputs}/Input/{ISO}/{ISO}_wc2.1_30s_tmin.tif, ..._tmax.tif         WorldClim 2.1, 12 bands
    {inputs}/Global/rwi.tif                                             Relative Wealth Index
The two small country tables (HDI, household size) are in sample_generation/tables/.

Written by Matteo Pieraccini (Nov 2026, branch `input`); moved into main with a command-line
country, configurable paths and the provenance record (checklist C24, C25).
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import fiona
import numpy as np
import pandas as pd
import rasterio
from rasterio import features
from scipy.ndimage import label
from scipy.spatial import cKDTree
from shapely.geometry import Point


REPO_ROOT = Path(__file__).resolve().parents[1]
TABLES_ROOT = Path(__file__).resolve().parent / "tables"
HDI_PATH = TABLES_ROOT / "hdi_values.csv"
# Average household size per country: UN DESA (2022), Database on Household Size and Composition,
# latest observation; built by build_household_size_table.py.
HOUSEHOLD_PATH = TABLES_ROOT / "household_size_undesa2022.csv"
DEFAULT_OUT_ROOT = REPO_ROOT / "data" / "sample_input_2025"
INPUTS_ENV = "MGPY2_SAMPLE_INPUTS"
# Population year of the WorldPop layer (SPARSE clusters). Same year as the GHSL clusters
# (GHS-POP R2023A, 2025 epoch), so one sample does not mix two years. Every year of WorldPop
# R2025A (2015-2030) is modelled from the c.2010 and c.2020 census rounds and aligned to UN WPP
# 2024; 2020 is not an observed year either. Get the files with fetch_worldpop.py.
WORLDPOP_YEAR = 2025
POPULATION_THRESHOLD = 300
CONNECTIVITY = 8
SCHOOL_DISTANCE_M = 250
# Numbering of `cl_type` in the M-LED cluster tables (Husein et al. 2024). Kept for reference:
# the classes are recomputed below so that SPARSE clusters get one too. Do NOT read the
# `climate_class` column for this: it is a Koeppen-Geiger code (0-29), not a cooling class.
COOLING_MAPPING = {1: "AY", 2: "AS", 3: "OM", 4: "NC"}
TIER_COLUMNS = [f"h_tier{i}" for i in range(1, 6)]
OUTPUT_COLUMNS = [
    "cat", "school_fid", *TIER_COLUMNS, "hospital_1", "hospital_2", "hospital_3",
    "hospital_4", "hospital_5", "schools", "school_total_demand", "lat",
    "lon", "distance_grid_km", "pop_sum", "density", "cooling", "rwi",
    "rwi_std",
]


def worldpop_filename(country: str, year: int = WORLDPOP_YEAR) -> str:
    """WorldPop R2025A, constrained, UN-adjusted, 1 km: the file name used by worldpop.org."""
    return f"{country.lower()}_pop_{year}_CN_1km_R2025A_UA_v1.tif"


@dataclass(frozen=True)
class CountryInputs:
    """Every source file of one country. Built once per country instead of module-level
    constants, so the script can run any country (or several) without being edited."""
    country: str
    ghsl: Path
    schools: Path
    gadm: Path
    worldpop: Path
    tmin: Path
    tmax: Path
    rwi: Path

    @classmethod
    def for_country(cls, inputs_root: Path, country: str) -> "CountryInputs":
        folder = inputs_root / "Input" / country
        return cls(
            country=country,
            ghsl=folder / f"{country}_Population_Clusters_EPSG4326.gpkg.zip",
            schools=folder / f"schools_{country}.gpkg",
            gadm=folder / f"gadm41_{country}.gpkg",
            worldpop=folder / worldpop_filename(country),
            tmin=folder / f"{country}_wc2.1_30s_tmin.tif",
            tmax=folder / f"{country}_wc2.1_30s_tmax.tif",
            rwi=inputs_root / "Global" / "rwi.tif",
        )

    def files(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if isinstance(v, Path)}

    def missing(self) -> list:
        """Missing files; the GHSL archive may also be present unzipped."""
        out = []
        for name, path in self.files().items():
            if not path.exists() and not (name == "ghsl" and path.with_suffix("").exists()):
                out.append(str(path))
        return out


def load_country_tables():
    hdi = pd.read_csv(HDI_PATH)
    household = pd.read_csv(HOUSEHOLD_PATH)
    hdi_min = hdi.hdi_value.min()
    hdi_max = hdi.hdi_value.max()
    adjustments = {
        row.country: 0.0 if hdi_max == hdi_min else
        2 * (row.hdi_value - hdi_min) / (hdi_max - hdi_min) - 1
        for row in hdi.itertuples()
    }
    sizes = household.set_index("iso3")["household_size"].dropna().to_dict()
    return adjustments, sizes


def finest_admin_level(gadm_path):
    for level in [4, 3, 2, 1]:
        try:
            gpd.read_file(gadm_path, layer=f"ADM_ADM_{level}", rows=1)
            return level
        except Exception:
            continue
    raise ValueError(f"No administrative layer found: {gadm_path}")


def allocate_households(rwi, rwi_std, households, adjustment):
    standard_deviation = max(float(rwi_std), 0.1)
    x_values = np.linspace(-3.0, 3.0, 1000)
    center = float(rwi) + adjustment
    pdf = np.exp(-0.5 * ((x_values - center) / standard_deviation) ** 2)
    pdf /= pdf.sum()
    bins = [-2.5, -1.5, -0.5, 0.5, 1.5, 2.5]
    raw = np.array([
        pdf[(x_values >= bins[i]) & (x_values < bins[i + 1])].sum() * households
        for i in range(5)
    ])
    result = np.floor(raw).astype(int)
    order = np.argsort(-(raw - result))
    for index in range(int(households - result.sum())):
        result[order[index % len(order)]] += 1
    return result.tolist()


def reconcile_ghsl_duplicates(data, country):
    """Combine geometries only when duplicate IDs have identical attributes.

    GHSL population/density attributes describe the cluster as a whole; never
    sum them when polygon parts carry repeated cluster-level attributes.
    """
    repeated = data["cat"].duplicated(keep=False)
    if not repeated.any():
        return data

    from shapely.ops import unary_union

    pieces = []
    for cat, group in data.loc[repeated].groupby("cat", sort=False):
        attributes = group.drop(columns="geometry")
        # Treat NaNs as equal; any conflicting attribute must be investigated.
        disagreements = [
            column for column in attributes.columns
            if attributes[column].nunique(dropna=False) != 1
        ]
        if disagreements:
            raise ValueError(
                f"{country}: duplicate GHSL cat={cat} has conflicting "
                f"attributes {disagreements}; cannot merge safely"
            )
        if group.geometry.isna().any() or group.geometry.is_empty.any():
            raise ValueError(f"{country}: duplicate GHSL cat={cat} has empty geometry")
        merged = group.iloc[0].copy()
        merged["geometry"] = unary_union(list(group.geometry))
        # Coordinates can be absent for split polygons (BEN cat 1374).
        # A representative point is guaranteed to lie within the merged geometry.
        if pd.isna(merged["x"]) or pd.isna(merged["y"]):
            point = merged.geometry.representative_point()
            merged["x"], merged["y"] = point.x, point.y
        pieces.append(merged)
        print(f"{country}: combined {len(group)} polygon parts for GHSL cat={cat}")

    single = data.loc[~repeated].copy()
    combined = gpd.GeoDataFrame(pieces, geometry="geometry", crs=data.crs)
    result = pd.concat([single, combined], ignore_index=True)
    return gpd.GeoDataFrame(result, geometry="geometry", crs=data.crs)


def load_ghsl(inputs: CountryInputs):
    """Read GHSL attributes and geometries exclusively from the country GPKG."""
    country = inputs.country
    ghsl_path = inputs.ghsl
    if not ghsl_path.exists():
        alternate = ghsl_path.with_suffix("")
        if not alternate.exists():
            raise FileNotFoundError(f"{country}: missing {ghsl_path} (or {alternate})")
        source_path = str(alternate)
    else:
        source_path = f"zip://{ghsl_path}"
    layer = f"{country}_population_clusters_poly"
    data = gpd.read_file(source_path, layer=layer)
    required = {"cat", "x", "y", "POP_sum", "DEN_average", "distance_grid_km",
                "rwi", "health_first", "health_primary", "health_secondary", "health_tertiary"}
    missing = sorted(required - set(data.columns))
    if missing:
        raise ValueError(f"{country}: GHSL GPKG missing fields {missing}")
    data = reconcile_ghsl_duplicates(data, country)
    if data.cat.duplicated().any():
        raise ValueError(f"{country}: duplicate GHSL cat values remain after reconciliation")
    if data[["x", "y"]].isna().any().any():
        raise ValueError(f"{country}: missing GHSL coordinates after reconciliation")
    if data["rwi"].isna().any():
        raise ValueError(f"{country}: missing GHSL RWI values")
    geometry = data.set_index("cat")[["rwi", "geometry"]]
    return pd.DataFrame(data.drop(columns="geometry")), geometry


COOLING_BASE_TEMPERATURE_C = 26.0
COOLING_CDD_THRESHOLD = 90.0   # mean monthly CDD26 above which a half-year needs cooling
DAYS_PER_MONTH = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
APR_SEP = [3, 4, 5, 6, 7, 8]
OCT_MAR = [9, 10, 11, 0, 1, 2]


def monthly_cdd(tmin, tmax, base=COOLING_BASE_TEMPERATURE_C):
    """Monthly cooling degree days from monthly mean Tmin and Tmax.

    UK Met Office method (Spinoni et al. 2018) as used by Falchetta & Mistry (2021),
    Energy Economics 99, 105307, Table 1. Degree days per average day of the month,
    with Tmid = (Tmax + Tmin) / 2:

        Tmax <= base                  0
        Tmid <= base < Tmax           (Tmax - base) / 4
        Tmin <= base < Tmid           (Tmax - base) / 2 - (base - Tmin) / 4
        Tmin >= base                  Tmid - base

    The formula assumes a daily cycle symmetric between Tmin and Tmax, so the mean it
    uses is the midpoint; with the midpoint the four cases join without jumps (see the
    tests). WorldClim 2.1 Tavg equals the midpoint to within 0.05 C, so using it would
    change 11 of 212,626 clusters, but the midpoint is the definition and needs one
    raster fewer. Unlike max(Tavg - base, 0), this counts the hot hours of days whose
    mean stays below the base temperature. Arrays have shape (clusters, 12).
    """
    tmin, tmax = (np.asarray(a, dtype=float) for a in (tmin, tmax))
    tmid = (tmin + tmax) / 2
    per_day = np.zeros_like(tmid)
    warm_afternoon = (tmid <= base) & (tmax > base)
    per_day[warm_afternoon] = (tmax[warm_afternoon] - base) / 4
    cool_night = (tmin <= base) & (tmid > base)
    per_day[cool_night] = (tmax[cool_night] - base) / 2 - (base - tmin[cool_night]) / 4
    always_hot = tmin >= base
    per_day[always_hot] = tmid[always_hot] - base
    return per_day * DAYS_PER_MONTH


def cooling_class(cdd, threshold=COOLING_CDD_THRESHOLD):
    """Climate types of Husein et al. (2024), Heliyon 10, e27440, section 2.4.2.

    AY: mean monthly CDD26 above the threshold in both half-years; AS: only
    April-September; OM: only October-March; NC: neither.
    """
    apr_sep = cdd[:, APR_SEP].mean(axis=1) > threshold
    oct_mar = cdd[:, OCT_MAR].mean(axis=1) > threshold
    return np.select([apr_sep & oct_mar, apr_sep & ~oct_mar, ~apr_sep & oct_mar],
                     ["AY", "AS", "OM"], default="NC")


def sample_monthly_raster(path, frame, label=""):
    """12 monthly values per cluster; NoData takes the nearest cell valid in all months."""
    from scipy.ndimage import distance_transform_edt
    if not path.exists():
        raise FileNotFoundError(f"Missing WorldClim raster: {path}")
    with rasterio.open(path) as src:
        if src.count != 12:
            raise ValueError(f"{path}: expected 12 monthly bands, got {src.count}")
        coords = gpd.GeoSeries(gpd.points_from_xy(frame.lon, frame.lat), crs="EPSG:4326").to_crs(src.crs)
        samples = np.ma.array(list(src.sample([(p.x, p.y) for p in coords], masked=True)))
        vals = samples.filled(np.nan).astype(float)
        if src.nodata is not None:
            vals[vals == src.nodata] = np.nan
        missing = ~np.isfinite(vals).all(axis=1)
        if missing.any():
            all_bands = src.read(masked=True)
            valid = (~np.ma.getmaskarray(all_bands)).all(axis=0) & np.isfinite(all_bands.filled(np.nan)).all(axis=0)
            if not valid.any():
                raise ValueError(f"No valid climate cells in {path}")
            nearest = distance_transform_edt(~valid, return_distances=False, return_indices=True)
            for i in np.flatnonzero(missing):
                x, y = coords.iloc[i].x, coords.iloc[i].y
                row, col = src.index(x, y)
                row = np.clip(row, 0, src.height - 1)
                col = np.clip(col, 0, src.width - 1)
                rr, cc = nearest[:, row, col]
                vals[i] = all_bands[:, rr, cc].filled(np.nan)
            print(f"{label}: {path.name} nearest-valid-cell fallback for {missing.sum()} clusters")
    if not np.isfinite(vals).all():
        raise ValueError(f"{path}: missing climate data after nearest-cell fallback")
    return vals


def cooling_from_worldclim(frame, inputs: CountryInputs):
    """Cooling class per cluster from WorldClim 2.1 monthly Tmin and Tmax (1970-2000)."""
    tmin = sample_monthly_raster(inputs.tmin, frame, inputs.country)
    tmax = sample_monthly_raster(inputs.tmax, frame, inputs.country)
    return cooling_class(monthly_cdd(tmin, tmax))


def make_sparse(inputs: CountryInputs, ghsl_geometry, gadm_path):
    country = inputs.country
    worldpop_path, rwi_path = inputs.worldpop, inputs.rwi
    for path in (worldpop_path, rwi_path):
        if not path.exists():
            raise FileNotFoundError(f"{country}: missing {path}")
    with rasterio.open(worldpop_path) as source:
        mask_geometry = ghsl_geometry.dissolve().to_crs(source.crs)
        data = source.read(1)
        valid = np.isfinite(data)
        if source.nodata is not None:
            valid &= data != source.nodata
        outside = ~features.geometry_mask(
            mask_geometry.geometry,
            out_shape=data.shape,
            transform=source.transform,
            invert=True,
            all_touched=True,
        )
        valid &= outside & (data >= POPULATION_THRESHOLD)
        structure = np.ones((3, 3), dtype=int) if CONNECTIVITY == 8 else np.array(
            [[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=int
        )
        clusters, count = label(valid, structure=structure)
        records = []
        for cluster_id in range(1, count + 1):
            rows, cols = np.where(clusters == cluster_id)
            if len(rows) == 0:
                continue
            population = float(data[rows, cols].sum())
            density = population / len(rows)
            x, y = rasterio.transform.xy(
                source.transform, rows.mean(), cols.mean(), offset="center"
            )
            records.append({"POP": population, "DEN": density, "geometry": Point(x, y)})
        sparse = gpd.GeoDataFrame(records, crs=source.crs).to_crs(4326)

    with rasterio.open(rwi_path) as source:
        points = sparse.to_crs(source.crs)
        sampled = []
        for point in points.geometry:
            value = next(source.sample([(point.x, point.y)]))[0]
            sampled.append(np.nan if source.nodata == value or np.isnan(value) else float(value))
    sparse["rwi"] = sampled
    level = finest_admin_level(gadm_path)
    admin_id = f"GID_{level}"
    admin = gpd.read_file(gadm_path, layer=f"ADM_ADM_{level}").to_crs(sparse.crs)
    joined = gpd.sjoin(sparse, admin[[admin_id, "geometry"]], how="left", predicate="within")
    means = joined.groupby(admin_id).rwi.mean()
    country_mean = sparse.rwi.mean()
    for index in sparse.index[sparse.rwi.isna()]:
        admin_value = joined.loc[index, admin_id] if index in joined.index else None
        sparse.loc[index, "rwi"] = means.get(admin_value, np.nan)
        if pd.isna(sparse.loc[index, "rwi"]):
            sparse.loc[index, "rwi"] = country_mean
    sparse["cat"] = [f"SPARSE_{index + 1}" for index in sparse.index]
    return sparse[["cat", "POP", "DEN", "rwi", "geometry"]]


def assign_schools(inputs: CountryInputs, ghsl_geometry, ghsl_cats, projected_crs):
    country = inputs.country
    schools_path = inputs.schools
    if not schools_path.exists():
        raise FileNotFoundError(f"{country}: missing {schools_path}")
    schools = gpd.read_file(schools_path)
    if "dem_el_wfi" not in schools.columns:
        raise ValueError(f"{country}: school input needs dem_el_wfi")
    with fiona.open(schools_path) as source:
        feature_ids = [feature["id"] for feature in source]
    if len(feature_ids) != len(schools):
        raise ValueError(f"{country}: school feature ID count does not match rows")
    schools = schools.reset_index(drop=True)
    schools["school_index"] = schools.index
    schools["school_fid"] = feature_ids
    schools = schools.to_crs(projected_crs)
    polygons = ghsl_geometry.loc[ghsl_geometry.index.isin(ghsl_cats)].reset_index()
    polygons = polygons.to_crs(projected_crs)
    candidates = gpd.sjoin_nearest(
        schools,
        polygons[["cat", "geometry"]],
        how="left",
        max_distance=SCHOOL_DISTANCE_M,
        distance_col="distance_m",
    )
    assigned = candidates.sort_values("distance_m").drop_duplicates("school_index")
    assigned = assigned[assigned["cat"].notna()]
    assigned_ids = set(assigned.school_index)
    unassigned = schools[~schools.school_index.isin(assigned_ids)].copy()
    totals = assigned.groupby("cat").agg(
        schools=("school_index", "size"),
        school_total_demand=("dem_el_wfi", lambda values: values.sum() * 1000),
        school_fid=("school_fid", lambda values: ";".join(str(value) for value in sorted(values))),
    )
    return schools, unassigned, totals, polygons


def school_records(unassigned, projected_crs):
    if unassigned.empty:
        return []
    coordinates = np.array([[point.x, point.y] for point in unassigned.geometry])
    labels = np.arange(len(unassigned))
    tree = cKDTree(coordinates)
    for first, second in tree.query_pairs(SCHOOL_DISTANCE_M):
        labels[labels == labels[second]] = labels[first]
    records = []
    for cluster_id in np.unique(labels):
        cluster = unassigned.iloc[np.where(labels == cluster_id)[0]]
        centroid = cluster.geometry.union_all().centroid
        centroid = gpd.GeoSeries([centroid], crs=projected_crs).to_crs(4326).iloc[0]
        records.append({
            "cat": f"SCHOOL_{cluster_id + 1}",
            "school_fid": ";".join(str(value) for value in sorted(cluster.school_fid)),
            "schools": len(cluster),
            "school_total_demand": float(cluster.dem_el_wfi.sum()) * 1000,
            "lat": centroid.y, "lon": centroid.x,
        })
    return records


def calculate_std(population, gadm_path):
    points = gpd.GeoDataFrame(
        population,
        geometry=gpd.points_from_xy(population.lon, population.lat),
        crs="EPSG:4326",
    )
    level = finest_admin_level(gadm_path)
    admin_id = f"GID_{level}"
    admin = gpd.read_file(gadm_path, layer=f"ADM_ADM_{level}").to_crs(points.crs)
    joined = gpd.sjoin(points, admin[[admin_id, "geometry"]], how="left", predicate="within")
    overall = population.rwi.std() if len(population) > 1 else 0.0
    unit_std = joined.groupby(admin_id).rwi.std().replace(0, np.nan)
    values = joined[admin_id].map(unit_std).fillna(overall)
    return values.groupby(level=0).first().reindex(population.index).fillna(overall)


def generate_country(inputs: CountryInputs, adjustments, household_sizes, output_path: Path):
    country = inputs.country
    gadm_path = inputs.gadm
    if not gadm_path.exists():
        raise FileNotFoundError(f"{country}: missing {gadm_path}")
    loads, geometry = load_ghsl(inputs)
    sparse = make_sparse(inputs, geometry, gadm_path)
    projected_crs = geometry.estimate_utm_crs()
    _, unassigned, school_totals, polygons = assign_schools(inputs, geometry, loads["cat"], projected_crs)

    ghsl_records = []
    for row in loads.itertuples(index=False):
        ghsl_records.append({
            "cat": f"GHSL_{int(row.cat)}", "lat": row.y, "lon": row.x,
            "school_fid": "",
            "distance_grid_km": row.distance_grid_km, "pop_sum": row.POP_sum,
            "density": row.DEN_average, "rwi": row.rwi,
            "cooling": None, "schools": 0,
            "school_total_demand": 0.0, "hospital_1": int(0 if pd.isna(row.health_primary) else row.health_primary),
            "hospital_2": int((0 if pd.isna(row.health_first) else row.health_first) + (0 if pd.isna(row.health_secondary) else row.health_secondary)),
            "hospital_3": int(0 if pd.isna(row.health_tertiary) else row.health_tertiary), "hospital_4": 0,
            "hospital_5": 0,
        })
    ghsl = pd.DataFrame(ghsl_records)
    for cat, values in school_totals.iterrows():
        mask = ghsl["cat"] == f"GHSL_{int(cat)}"
        ghsl.loc[mask, "schools"] = int(values.schools)
        ghsl.loc[mask, "school_total_demand"] = float(values.school_total_demand)
        ghsl.loc[mask, "school_fid"] = values.school_fid

    sparse_records = []
    for index, row in sparse.iterrows():
        sparse_records.append({
            "cat": row["cat"], "lat": row.geometry.y, "lon": row.geometry.x,
            "distance_grid_km": np.nan, "pop_sum": row.POP, "density": row.DEN,
            "rwi": row.rwi, "cooling": None, "schools": 0,
            "school_total_demand": 0.0, "hospital_1": 0, "hospital_2": 0,
            "hospital_3": 0, "hospital_4": 0, "hospital_5": 0,
        })
    sparse_frame = pd.DataFrame(sparse_records)
    population = pd.concat([ghsl, sparse_frame], ignore_index=True)
    if population.rwi.isna().any():
        raise ValueError(f"{country}: missing population RWI for {population.rwi.isna().sum()} clusters")
    population["cooling"] = cooling_from_worldclim(population, inputs)
    population["rwi_std"] = calculate_std(population, gadm_path)
    for index in population.index:
        households = int(round(population.loc[index, "pop_sum"] / household_sizes[country]))
        population.loc[index, TIER_COLUMNS] = allocate_households(
            population.loc[index, "rwi"], population.loc[index, "rwi_std"],
            households, adjustments[country],
        )
    output = pd.concat([population, pd.DataFrame(school_records(unassigned, projected_crs))], ignore_index=True)
    output = output.reindex(columns=OUTPUT_COLUMNS, fill_value=0)
    output["school_fid"] = output["school_fid"].replace(0, "")
    output.loc[output["cat"].str.startswith("SCHOOL_"), "cooling"] = "NA"
    if output.cat.duplicated().any():
        raise ValueError(f"{country}: duplicate output cluster IDs")
    if not set(population.cooling).issubset({"AY", "AS", "OM", "NC"}):
        raise ValueError(f"{country}: invalid cooling category")
    if (population[TIER_COLUMNS].fillna(0).to_numpy() < 0).any():
        raise ValueError(f"{country}: negative household tier counts")
    expected = np.rint(population.pop_sum.to_numpy() / household_sizes[country]).astype(int)
    actual = population[TIER_COLUMNS].sum(axis=1).to_numpy().astype(int)
    if not np.array_equal(actual, expected):
        raise ValueError(f"{country}: household tier totals mismatch")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_path, index=False)
    write_provenance(inputs, output_path, rows=len(output))
    print(f"{country}: {len(output)} rows written to {output_path}")


def sha256(path: Path, chunk=1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def write_provenance(inputs: CountryInputs, output_path: Path, rows: int) -> Path:
    """Record which files made this sample: name, size and SHA-256 of every input, the two
    tables, the code commit and the date. Written next to the sample."""
    files = {**inputs.files(), "hdi_table": HDI_PATH, "household_size_table": HOUSEHOLD_PATH}
    try:
        commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    record = {
        "country": inputs.country,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "code_commit": commit,
        "rows": rows,
        "inputs": {name: {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
                   for name, path in files.items() if path.exists()},
    }
    path = output_path.with_name(output_path.stem + ".provenance.json")
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("countries", nargs="+", help="ISO3 codes, e.g. ZWE KEN")
    parser.add_argument("--inputs", type=Path, default=os.environ.get(INPUTS_ENV),
                        help=f"folder with the source inputs (default: ${INPUTS_ENV})")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT,
                        help="writes {out-root}/{ISO}/advanced_sample.csv (default: %(default)s)")
    args = parser.parse_args(argv)
    if args.inputs is None:
        parser.error(f"give --inputs or set ${INPUTS_ENV}")
    adjustments, household_sizes = load_country_tables()
    for country in (c.upper() for c in args.countries):
        inputs = CountryInputs.for_country(args.inputs, country)
        missing = inputs.missing()
        if missing:
            raise FileNotFoundError(f"{country}: missing required inputs:\n" + "\n".join(missing))
        if country not in adjustments or country not in household_sizes:
            raise ValueError(f"{country}: missing HDI or household-size entry in {TABLES_ROOT}")
        generate_country(inputs, adjustments, household_sizes,
                         args.out_root / country / "advanced_sample.csv")


if __name__ == "__main__":
    main()
