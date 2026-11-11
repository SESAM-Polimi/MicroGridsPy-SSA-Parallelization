"""Generate advanced_sample.csv from the country-level source inputs.

The output root is separate from the existing samples by default. Change
OUTPUT_ROOT before running if a different destination is required.
"""

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


# Change ONLY this value when running a different country.
COUNTRY = "ZWE"
BASE_ROOT = Path(__file__).resolve().parents[1]
INPUT_ROOT = BASE_ROOT / "Input" / COUNTRY
GLOBAL_ROOT = BASE_ROOT / "Global"
GHSL_PATH = INPUT_ROOT / f"{COUNTRY}_Population_Clusters_EPSG4326.gpkg.zip"
SCHOOLS_PATH = INPUT_ROOT / f"schools_{COUNTRY}.gpkg"
GADM_PATH = INPUT_ROOT / f"gadm41_{COUNTRY}.gpkg"
WORLDPOP_RASTER = INPUT_ROOT / f"{COUNTRY.lower()}_pop_2020_CN_1km_R2025A_UA_v1.tif"
WORLDCLIM_TMIN_RASTER = INPUT_ROOT / f"{COUNTRY}_wc2.1_30s_tmin.tif"
WORLDCLIM_TMAX_RASTER = INPUT_ROOT / f"{COUNTRY}_wc2.1_30s_tmax.tif"
RWI_RASTER = GLOBAL_ROOT / "rwi.tif"
HDI_PATH = GLOBAL_ROOT / "hdi_values.csv"
HOUSEHOLD_PATH = GLOBAL_ROOT / "household_number.csv"
OUTPUT_PATH = BASE_ROOT / "sample" / f"{COUNTRY}_sample.csv"
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
    sizes = household.set_index("Country Code")["Mean national"].dropna().to_dict()
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


def load_ghsl(country):
    """Read GHSL attributes and geometries exclusively from the country GPKG."""
    if not GHSL_PATH.exists():
        alternate = GHSL_PATH.with_suffix("")
        if not alternate.exists():
            raise FileNotFoundError(f"{country}: missing {GHSL_PATH} (or {alternate})")
        source_path = str(alternate)
    else:
        source_path = f"zip://{GHSL_PATH}"
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


def sample_monthly_raster(path, frame):
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
            print(f"{COUNTRY}: {path.name} nearest-valid-cell fallback for {missing.sum()} clusters")
    if not np.isfinite(vals).all():
        raise ValueError(f"{path}: missing climate data after nearest-cell fallback")
    return vals


def cooling_from_worldclim(frame):
    """Cooling class per cluster from WorldClim 2.1 monthly Tmin and Tmax (1970-2000)."""
    tmin = sample_monthly_raster(WORLDCLIM_TMIN_RASTER, frame)
    tmax = sample_monthly_raster(WORLDCLIM_TMAX_RASTER, frame)
    return cooling_class(monthly_cdd(tmin, tmax))


def make_sparse(country, ghsl_geometry, gadm_path):
    if not WORLDPOP_RASTER.exists() or not RWI_RASTER.exists():
        raise FileNotFoundError("Missing explicit MRT WorldPop or RWI raster")
    with rasterio.open(WORLDPOP_RASTER) as source:
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

    with rasterio.open(RWI_RASTER) as source:
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


def assign_schools(country, ghsl_geometry, ghsl_cats, projected_crs):
    if not SCHOOLS_PATH.exists():
        raise FileNotFoundError("Missing explicit MRT school GeoPackage")
    schools = gpd.read_file(SCHOOLS_PATH)
    if "dem_el_wfi" not in schools.columns:
        raise ValueError(f"{country}: school input needs dem_el_wfi")
    with fiona.open(SCHOOLS_PATH) as source:
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


def generate_country(country, adjustments, household_sizes):
    if not GADM_PATH.exists():
        raise FileNotFoundError("Missing explicit MRT GADM GeoPackage")
    loads, geometry = load_ghsl(country)
    sparse = make_sparse(country, geometry, GADM_PATH)
    projected_crs = geometry.estimate_utm_crs()
    _, unassigned, school_totals, polygons = assign_schools(country, geometry, loads["cat"], projected_crs)

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
    population["cooling"] = cooling_from_worldclim(population)
    population["rwi_std"] = calculate_std(population, GADM_PATH)
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
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(OUTPUT_PATH, index=False)
    print(f"{country}: {len(output)} rows written to {OUTPUT_PATH}")


def main():
    required_paths = [SCHOOLS_PATH, GADM_PATH, WORLDPOP_RASTER,
                      WORLDCLIM_TMIN_RASTER, WORLDCLIM_TMAX_RASTER,
                      RWI_RASTER, HDI_PATH, HOUSEHOLD_PATH]
    missing = [str(p) for p in required_paths if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing required inputs:\n" + "\n".join(missing))
    adjustments, household_sizes = load_country_tables()
    if COUNTRY not in adjustments or COUNTRY not in household_sizes:
        raise ValueError(f"{COUNTRY}: missing HDI or household-size entry in Global tables")
    generate_country(COUNTRY, adjustments, household_sizes)


if __name__ == "__main__":
    main()