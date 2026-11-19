import pandas as pd
import numpy as np
from scipy.stats import norm
import geopandas as gpd
import os

# ============================================================
# USER INPUTS — CHANGE FOR EACH COUNTRY
# ============================================================

COUNTRY = "BEN"  # ISO3 country code (e.g. 'CMR' for Cameroon)pyth

CLUSTER_CSV = f"/Volumes/2TB/outputs_{COUNTRY}_advanced/sample.csv"
NATIONAL_RWI_CSV = f"/Users/matteo/Desktop/Tesi/qgis/relative-wealth-index-april-2021/{COUNTRY}_relative_wealth_index.csv"

# Subnational GINI and GNI GPKG files (with geometries)
GINI_GPKG = "/Users/matteo/Desktop/Tesi/qgis/polyg_adm1_gini_disp_SSA.gpkg"
GNI_GPKG = "/Users/matteo/Desktop/Tesi/qgis/polyg_adm1_gni_perCapita_SSA.gpkg"
# Year to use for national GNI columns in gni.csv (e.g. gni_nominal_2021)
YEAR = "2021"
# Year column to use for regional GINI (prefer 2021)
YEAR_GINI = "2021"

RWI_FIELD = "rwi"    # name of RWI field in BOTH files

OUTPUT_CSV = os.path.join(os.path.dirname(CLUSTER_CSV) or '.', f"{COUNTRY}_clusters_income.csv")

# Allow command-line overrides so this script can be automated across countries
import argparse
parser = argparse.ArgumentParser(description='Compute cluster incomes for a country')
parser.add_argument('--country', '-c', default=COUNTRY, help='ISO3 country code (overrides COUNTRY)')
parser.add_argument('--cluster-csv', help='Path to cluster CSV (overrides default CLUSTER_CSV)')
args, _ = parser.parse_known_args()
if args.country and args.country != COUNTRY:
    COUNTRY = args.country
    CLUSTER_CSV = f"/Volumes/2TB/outputs_{COUNTRY}_advanced/sample.csv"
    NATIONAL_RWI_CSV = f"/Users/matteo/Desktop/Tesi/qgis/relative-wealth-index-april-2021/{COUNTRY}_relative_wealth_index.csv"
    OUTPUT_CSV = os.path.join(os.path.dirname(CLUSTER_CSV) or '.', f"{COUNTRY}_clusters_income.csv")
if args.cluster_csv:
    CLUSTER_CSV = args.cluster_csv
    OUTPUT_CSV = os.path.join(os.path.dirname(CLUSTER_CSV) or '.', f"{COUNTRY}_clusters_income.csv")

# Tail/shape controls
GAMMA = 0.32           # Pareto weight; paper optimal value (0.10 was conservative damping)
RANK_MIN = 0.02        # lower bound on quantile
RANK_MAX = 0.98        # upper bound on quantile (paper used 0.98; 0.90 was conservative)
CAP_MULTIPLE = None    # cap income at this multiple of regional GNI (paper used None)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def gini_to_sigma(gini):
    """Convert Gini to sigma for lognormal distribution"""
    return np.sqrt(2) * norm.ppf((gini + 1) / 2)

def gini_to_alpha(gini):
    """Convert Gini to alpha for Pareto distribution"""
    return (1 + gini) / (2 * gini)

def wealth_from_pareto(rank, wealth_percap, gini):
    """Calculate wealth using Pareto distribution (Equation 1 + 2)"""
    alpha = gini_to_alpha(gini)
    threshold = (1 - (1/alpha)) * wealth_percap
    
    # Inverse CDF for Pareto
    wealth = threshold / ((1 - rank) ** (1/alpha))
    return wealth

def wealth_from_lognormal(rank, wealth_percap, gini):
    """Calculate wealth using Log-normal distribution (Equation 1 + 3)"""
    sigma = gini_to_sigma(gini)
    mu = np.log(wealth_percap) - (sigma**2) / 2
    
    # Inverse CDF for log-normal
    wealth = np.exp(mu + sigma * norm.ppf(rank))
    return wealth

def wealth_combined(rank, wealth_percap, gini, gamma=0.5):
    """
    Calculate wealth using combined Pareto-Lognormal (Equation 4)
    gamma = 0: pure lognormal
    gamma = 1: pure Pareto
    gamma = 0.5: equal weighting (default)
    """
    w_pareto = wealth_from_pareto(rank, wealth_percap, gini)
    w_lognormal = wealth_from_lognormal(rank, wealth_percap, gini)
    
    # Geometric mean weighted by gamma
    wealth = (w_lognormal ** (1 - gamma)) * (w_pareto ** gamma)
    return wealth

def classify_cluster(cat):
    if isinstance(cat, str):
        if cat.startswith("GHSL_"):
            return "urban"
        if cat.startswith("SPARSE_"):
            return "rural"
        if cat.startswith("SCHOOL_"):
            return "school"
    return "unknown"


# ============================================================
# LOAD INPUT FILES
# ============================================================

print("Loading cluster data...")
clusters_df = pd.read_csv(CLUSTER_CSV)

# Create GeoDataFrame from lat/lon
print("Converting to GeoDataFrame...")
from shapely.geometry import Point
geometry = [Point(xy) for xy in zip(clusters_df['lon'], clusters_df['lat'])]
clusters = gpd.GeoDataFrame(clusters_df, geometry=geometry, crs="EPSG:4326")

print("Loading national RWI data...")
rwi_nat = pd.read_csv(NATIONAL_RWI_CSV)

print(f"Loading GINI regions for {COUNTRY} (year {YEAR_GINI})...")
gini_all = gpd.read_file(GINI_GPKG, encoding='latin-1')
gini_country = gini_all[gini_all['iso3'] == COUNTRY].copy()
print(f"  Found {len(gini_country)} GINI regions")

print("Subnational GNI per-capita values are not used; skipping GNI GPKG read.")

# ============================================================
# STEP 1: ASSIGN REGIONS USING GADM (PRIMARY SOURCE)
# ============================================================

print(f"\nStep 1: Assigning regions using GADM...")
gadm_path = f'/Users/matteo/Desktop/Tesi/GADM/gadm41_{COUNTRY}.gpkg'
gadm = gpd.read_file(gadm_path, layer='ADM_ADM_1')  # admin level 1

# Ensure same CRS
gadm = gadm.to_crs(clusters.crs)
if clusters.crs != gini_country.crs:
    clusters = clusters.to_crs(gini_country.crs)
    gadm = gadm.to_crs(gini_country.crs)

# Spatial join all clusters to GADM
clusters_with_region = gpd.sjoin(clusters, gadm[['NAME_1', 'geometry']], how='left', predicate='intersects')
clusters_with_region.rename(columns={'NAME_1': 'gadm_region'}, inplace=True)
clusters_with_region = clusters_with_region.drop(columns=['index_right'], errors='ignore')

print(f"  GADM regions assigned: {clusters_with_region['gadm_region'].notna().sum()} / {len(clusters_with_region)}")

# If some clusters are outside GADM (no gadm_region), assign nearest region as a fallback
missing_mask = clusters_with_region['gadm_region'].isna()
fallback_count = 0
if missing_mask.sum() > 0:
    fallback_count = int(missing_mask.sum())
    print(f"  {fallback_count} clusters outside GADM boundaries — assigning nearest region fallback")
    # Reproject to metric CRS for distance calculation
    try:
        gadm_m = gadm.to_crs('EPSG:3857')
        pts_m = clusters_with_region[missing_mask].to_crs('EPSG:3857')
    except Exception:
        gadm_m = gadm
        pts_m = clusters_with_region[missing_mask]

    nearest_names = []
    nearest_dists_km = []
    for geom in pts_m.geometry:
        dists = gadm_m.geometry.distance(geom)
        idxmin = dists.idxmin()
        nearest_names.append(str(gadm_m.loc[idxmin, 'NAME_1']))
        nearest_dists_km.append(float(dists.loc[idxmin]) / 1000.0)

    # assign back to DataFrame
    clusters_with_region.loc[missing_mask, 'gadm_region'] = nearest_names
    clusters_with_region.loc[missing_mask, 'nearest_region_dist_km'] = nearest_dists_km
    print(f"  Assigned nearest regions for {fallback_count} clusters")

# ============================================================
# STEP 2: CREATE LOOKUP TABLES FROM GINI/GNI DATA
# ============================================================

print(f"\nStep 2: Creating GINI/GNI lookup tables...")

# GINI lookup: region_name → gini_value
# Note: GINI GPKG uses 'Subnat' column for region names
gini_lookup = {}
for idx, row in gini_country.iterrows():
    region_name = row['Subnat']  # ✓ GINI file has 'Subnat' not 'region'
    gini_value = row[YEAR_GINI]
    if pd.notna(region_name) and pd.notna(gini_value):
        gini_lookup[region_name.strip().lower()] = gini_value

# (Removed subnational GNI per-capita lookup — we no longer use
# regional GNI values for scaling. National GNI from `gni.csv`
# will be used instead; regional GINI still used for distribution shape.)
print(f"  GINI lookup: {len(gini_lookup)} regions")

# ============================================================
# Geometry-based GINI assignment: assign cluster->GINI using
# GINI polygons first (point-in-polygon), then nearest-polygon
# fallback for uncovered clusters.
# ============================================================
try:
    # choose which GINI column to use: prefer YEAR_GINI if present and non-null,
    # otherwise pick the first numeric column available
    year_col = None
    if YEAR_GINI in gini_country.columns and gini_country[YEAR_GINI].notna().any():
        year_col = YEAR_GINI
    else:
        candidate_cols = [c for c in gini_country.columns if c not in ('geometry','iso3','Country','Subnat','admID','id','slope')]
        numeric_cols = [c for c in candidate_cols if pd.api.types.is_numeric_dtype(gini_country[c])]
        if numeric_cols:
            year_col = numeric_cols[0]

    if year_col is not None:
        gini_country = gini_country.copy()
        gini_country['gini_polygon'] = gini_country[year_col].astype(float)

        # spatial join: point-in-polygon assignment
        try:
            # ensure a `gini` column exists for assignment
            if 'gini' not in clusters_with_region.columns:
                clusters_with_region['gini'] = np.nan

            joined = gpd.sjoin(clusters_with_region, gini_country[['gini_polygon','Subnat','geometry']], how='left', predicate='intersects')
            assigned_poly = int(joined['gini_polygon'].notna().sum())
            # attach polygon gini where available (preserve alignment)
            clusters_with_region['gini_polygon'] = joined['gini_polygon']
            mask_to_fill = clusters_with_region['gini'].isna() & clusters_with_region['gini_polygon'].notna()
            clusters_with_region.loc[mask_to_fill, 'gini'] = clusters_with_region.loc[mask_to_fill, 'gini_polygon']
            print(f"  Assigned {assigned_poly} clusters from GINI polygons by intersection")
        except Exception as e:
            print('  GINI polygon intersection failed:', e)

        # nearest-polygon fallback for any remaining non-school clusters
        missing_mask_poly = clusters_with_region['gini'].isna() & (clusters_with_region['cat'].apply(classify_cluster) != 'school')
        if missing_mask_poly.sum() > 0:
            gini_polys = gini_country[gini_country['gini_polygon'].notna()].copy()
            if not gini_polys.empty:
                try:
                    pts_m = clusters_with_region[missing_mask_poly].to_crs('EPSG:3857')
                    polys_m = gini_polys.to_crs('EPSG:3857')
                    # use sjoin_nearest if available
                    try:
                        nearest = gpd.sjoin_nearest(pts_m, polys_m[['gini_polygon','Subnat','geometry']], how='left', distance_col='dist_m')
                        n_assigned = int(nearest['gini_polygon'].notna().sum())
                        # assign back (preserve index alignment)
                        clusters_with_region.loc[missing_mask_poly, 'gini'] = nearest['gini_polygon'].values
                        clusters_with_region.loc[missing_mask_poly, 'gini_assigned_from_polygon'] = nearest['Subnat'].values
                        clusters_with_region.loc[missing_mask_poly, 'gini_assigned_distance_km'] = nearest['dist_m'].values / 1000.0
                        print(f"  Nearest-polygon fallback assigned {n_assigned} clusters")
                    except Exception:
                        # fallback: brute-force nearest by centroid distances
                        polys_m['centroid'] = polys_m.geometry.centroid
                        assigned = 0
                        nearest_regions = []
                        nearest_dists = []
                        assigned_ginis = []
                        for geom in pts_m.geometry:
                            dists = polys_m['centroid'].distance(geom)
                            idxmin = dists.idxmin()
                            nearest_regions.append(polys_m.loc[idxmin, 'Subnat'])
                            nearest_dists.append(float(dists.loc[idxmin]) / 1000.0)
                            assigned_ginis.append(polys_m.loc[idxmin, 'gini_polygon'])
                            assigned += 1
                        clusters_with_region.loc[missing_mask_poly, 'gini'] = assigned_ginis
                        clusters_with_region.loc[missing_mask_poly, 'gini_assigned_from_polygon'] = nearest_regions
                        clusters_with_region.loc[missing_mask_poly, 'gini_assigned_distance_km'] = nearest_dists
                        print(f"  Nearest-polygon fallback (centroid method) assigned {assigned} clusters")
                except Exception as e:
                    print('  Nearest-polygon fallback failed:', e)
            else:
                print('  No GINI polygons with numeric values available for nearest-polygon fallback')
    else:
        print('  No numeric GINI column found in GINI geopackage; skipping polygon assignment')
except Exception as e:
    print('  Geometry-based GINI assignment failed:', e)

# ============================================================
# AREA-OVERLAP MAPPING: derive NAME_1 -> Subnat mapping by geometry
# This covers cases where GADM has newer/split units not present
# in the GINI Subnat table (we map them to the best-overlapping Subnat)
# ============================================================
try:
    gadm_path = f'/Users/matteo/Desktop/Tesi/GADM/gadm41_{COUNTRY}.gpkg'
    gadm_try = gpd.read_file(gadm_path, layer='ADM_ADM_1')
    # prepare minimal GeoDataFrames
    gadm_m = gadm_try[['NAME_1', 'geometry']].copy()
    gini_m = gini_country[['Subnat', 'geometry']].copy()
    if gadm_m.crs is None:
        gadm_m.set_crs('EPSG:4326', inplace=True)
    if gini_m.crs is None:
        gini_m.set_crs('EPSG:4326', inplace=True)
    gadm_proj = gadm_m.to_crs('EPSG:3857')
    gini_proj = gini_m.to_crs('EPSG:3857')
    inter = gpd.overlay(gadm_proj, gini_proj, how='intersection')
    if not inter.empty:
        inter['area'] = inter.geometry.area
        agg = inter.groupby('NAME_1').apply(lambda g: g.loc[g['area'].idxmax(), ['Subnat','area']])
        agg = agg.reset_index()
        agg.columns = ['NAME_1','best_Subnat','area']
        gadm_proj['gadm_area'] = gadm_proj.geometry.area
        agg = agg.merge(gadm_proj[['NAME_1','gadm_area']], on='NAME_1', how='left')
        agg['frac'] = agg['area'] / agg['gadm_area']
        # Insert mapped names into gini_lookup using normalized keys
        added = 0
        for _, r in agg.iterrows():
            gadm_name = r['NAME_1']
            subnat = r['best_Subnat']
            key_gadm = normalize_name(gadm_name)
            key_sub = normalize_name(subnat)
            # if subnat exists in gini_lookup, copy its value under gadm normalized name
            if key_sub in gini_lookup:
                gini_lookup[key_gadm] = gini_lookup[key_sub]
                added += 1
        if added > 0:
            print(f"  Area-overlap mapping added {added} GADM->GINI name mappings")
except Exception:
    # if any error occurs, silently continue using existing name matching logic
    pass

# ============================================================
# STEP 3: MATCH GADM REGIONS TO GINI/GNI VALUES
# ============================================================

print(f"\nStep 3: Matching GADM regions to GINI/GNI values...")

def normalize_name(name):
    """Normalize region name by removing accents and converting to lowercase"""
    if pd.isna(name):
        return None
    import unicodedata
    # Remove accents
    nfkd = unicodedata.normalize('NFKD', str(name))
    ascii_str = ''.join([c for c in nfkd if not unicodedata.combining(c)])
    # Replace hyphens with spaces and collapse multiple spaces
    normalized = ascii_str.replace('-', ' ').strip().lower()
    normalized = ' '.join(normalized.split())  # collapse multiple spaces
    return normalized

# Common aliases for admin name changes / splits (normalized form)
# Map newer GADM names to existing GINI Subnat names when GINI lacks the newer unit.
ADMIN_NAME_ALIASES = {
    # Senegal (created regions mapped to parent Subnat)
    'kaffrine': 'kaolack',
    'kedougou': 'tambacounda',
    'matam': 'saint louis',
    'sedhiou': 'kolda',
}

def find_best_match(gadm_name, lookup_dict):
    """Find best matching region in lookup dictionary"""
    if pd.isna(gadm_name):
        return None
    
    gadm_normalized = normalize_name(gadm_name)
    
    # Try exact match with normalized names
    for key, value in lookup_dict.items():
        key_normalized = normalize_name(key)
        if gadm_normalized == key_normalized:
            return value
    
    # Special case: Wouleu vs Woleu
    if 'wouleu' in gadm_normalized:
        gadm_alt = gadm_normalized.replace('wouleu', 'woleu')
        for key, value in lookup_dict.items():
            if normalize_name(key) == gadm_alt:
                return value
    
    # Try partial match
    for key, value in lookup_dict.items():
        key_normalized = normalize_name(key)
        if gadm_normalized in key_normalized or key_normalized in gadm_normalized:
            return value

    # Fuzzy match using difflib for approximate name matches
    try:
        import difflib
        keys_norm = [normalize_name(k) for k in lookup_dict.keys()]
        matches = difflib.get_close_matches(gadm_normalized, keys_norm, n=1, cutoff=0.6)
        if matches:
            # find the original key corresponding to the normalized match
            matched_norm = matches[0]
            for orig_key, val in lookup_dict.items():
                if normalize_name(orig_key) == matched_norm:
                    return val
    except Exception:
        pass

    # Final attempt: apply admin name aliases map (map gadm name -> existing subnat)
    try:
        if gadm_normalized in ADMIN_NAME_ALIASES:
            alias = ADMIN_NAME_ALIASES[gadm_normalized]
            for orig_key, val in lookup_dict.items():
                if normalize_name(orig_key) == alias:
                    return val
    except Exception:
        pass
    
    return None

# Preserve any existing `gini` values (e.g. from polygon assignment). Only
# fill missing values by matching GADM NAME_1 -> gini_lookup.
if 'gini' not in clusters_with_region.columns:
    clusters_with_region['gini'] = np.nan
gadm_matched_gini = clusters_with_region['gadm_region'].apply(lambda x: find_best_match(x, gini_lookup))
clusters_with_region['gini'] = clusters_with_region['gini'].fillna(gadm_matched_gini)
clusters_with_region['region'] = clusters_with_region['gadm_region']
# Report matched GINI only (subnational GNI not used)
print(f"  Matched GINI: {clusters_with_region['gini'].notna().sum()} / {len(clusters_with_region)}")

# ============================================================
# Nearest-region GINI fallback
# If some non-school clusters still have missing `gini`, assign the
# GINI of the nearest ADM1 region (using ADM1 centroids for distance).
# This covers cases where NAME matching and area-overlap did not produce
# a regional GINI value for that ADM unit.
# ============================================================
try:
    # determine candidate missing clusters (exclude schools)
    missing_gini_mask = clusters_with_region['gini'].isna() & (clusters_with_region['cat'].apply(classify_cluster) != 'school')
    n_missing_gini = int(missing_gini_mask.sum())
    gini_fallback_count = 0
    if n_missing_gini > 0:
        # build ADM1 centroids and their mapped GINI (via find_best_match)
        adm1 = gadm[['NAME_1', 'geometry']].copy()
        if adm1.crs is None:
            adm1.set_crs(clusters.crs, inplace=True)

        # compute gini for each ADM1 where possible
        adm1['gini_mapped'] = adm1['NAME_1'].apply(lambda n: find_best_match(n, gini_lookup))
        adm1_with_gini = adm1[adm1['gini_mapped'].notna()].copy()

        if not adm1_with_gini.empty:
            # project to metric CRS for distance computations
            adm1_m = adm1_with_gini.to_crs('EPSG:3857')
            adm1_m['centroid'] = adm1_m.geometry.centroid

            pts_m = clusters_with_region[missing_gini_mask].to_crs('EPSG:3857')

            nearest_regions = []
            nearest_dists = []
            assigned_ginis = []

            for geom in pts_m.geometry:
                dists = adm1_m['centroid'].distance(geom)
                idxmin = dists.idxmin()
                nearest_regions.append(adm1_m.loc[idxmin, 'NAME_1'])
                nearest_dists.append(float(dists.loc[idxmin]) / 1000.0)
                assigned_ginis.append(adm1_m.loc[idxmin, 'gini_mapped'])

            # assign back to original frame
            clusters_with_region.loc[missing_gini_mask, 'gini'] = assigned_ginis
            clusters_with_region.loc[missing_gini_mask, 'gini_assigned_from_region'] = nearest_regions
            clusters_with_region.loc[missing_gini_mask, 'gini_assigned_distance_km'] = nearest_dists
            gini_fallback_count = int(len(assigned_ginis))
            print(f"  GINI nearest-region fallback applied to {gini_fallback_count} clusters")
        else:
            print("  No ADM1 regions with mapped GINI available for nearest-region fallback")
    else:
        print("  No non-school clusters with missing GINI found — skipping nearest-region fallback")
except Exception as e:
    print("  Nearest-region GINI fallback failed:", e)

# National GNI CSV (country-level values)
GNI_CSV = "/Users/matteo/Desktop/Tesi/Data sheet/gni.csv"
print(f"Loading national GNI from {GNI_CSV}...")
try:
    col_nom = f"gni_nominal_{YEAR}"
    col_ppp = f"gni_ppp_{YEAR}"
    _gni_tbl = pd.read_csv(GNI_CSV, dtype=str).set_index('iso3')
    national_nominal = float(_gni_tbl.loc[COUNTRY, col_nom]) if (COUNTRY in _gni_tbl.index and pd.notna(_gni_tbl.loc[COUNTRY, col_nom])) else np.nan
    national_ppp = float(_gni_tbl.loc[COUNTRY, col_ppp]) if (COUNTRY in _gni_tbl.index and pd.notna(_gni_tbl.loc[COUNTRY, col_ppp])) else np.nan
except Exception as e:
    print('  Could not read national GNI table:', e)
    national_nominal = np.nan
    national_ppp = np.nan

print(f"  national_nominal={national_nominal}, national_ppp={national_ppp}")
# Do NOT fill missing regional values with national fallbacks — use regional GINI where available,
# and use national GNI (from `gni.csv`) for scaling (no subnational GNI fallbacks).
print(f"  Matched GINI: {clusters_with_region['gini'].notna().sum()} / {len(clusters_with_region)}")

clusters_with_both = clusters_with_region

print(f"Regional GINI range: {clusters_with_both['gini'].min():.3f} - {clusters_with_both['gini'].max():.3f}")

# ============================================================
# HANDLE CLUSTERS OUTSIDE GADM BOUNDARIES
# ============================================================

# Clusters with no region will be handled separately in income calculation
# They already have national average gini/gni from the fillna above
missing_region_mask = clusters_with_both['region'].isna()
if missing_region_mask.sum() > 0:
    print(f"\nFound {missing_region_mask.sum()} clusters outside GADM boundaries")
    print(f"  Will use national average values for income calculation")

# ============================================================
# BUILD NATIONAL RWI CDF
# ============================================================

rwi_nat["rank"] = rwi_nat[RWI_FIELD].rank(method="average")
rwi_nat["p"] = rwi_nat["rank"] / len(rwi_nat)

# Build lookup table sorted by RWI
cdf_map = rwi_nat.set_index(RWI_FIELD)["p"].sort_index()

def rwi_to_quantile(value):
    idx = cdf_map.index.searchsorted(value)
    idx = np.clip(idx, 0, len(cdf_map)-1)
    return cdf_map.iloc[idx]


# ============================================================
# CLASSIFY CLUSTERS
# ============================================================

clusters_with_both["type"] = clusters_with_both["cat"].apply(classify_cluster)


# ============================================================
# COMPUTE INCOME PER CLUSTER
# ============================================================

print("Computing income per cluster using NATIONAL GNI and regional GINI...")
if 'fallback_count' in locals() and fallback_count:
    print(f"Nearest-region fallback assigned: {fallback_count} clusters")

# First pass: calculate raw wealth values (nominal and ppp) for all non-school clusters
raw_nominal_list = []
raw_ppp_list = []
cluster_indices = []

for idx, row in clusters_with_both.iterrows():
    ctype = row["type"]
    if ctype == "school":
        continue

    # Regional GINI shapes the distribution
    gini_cluster = row['gini']

    # National GNI used for scale (may be NaN)
    wealth_percap_nom = national_nominal
    wealth_percap_ppp = national_ppp

    # Get RWI rank
    rwi_val = row[RWI_FIELD]
    rank = rwi_to_quantile(rwi_val)
    rank = np.clip(rank, RANK_MIN, RANK_MAX)

    # Calculate raw wealth using combined distribution for nominal and PPP
    try:
        raw_nom = wealth_combined(rank, wealth_percap_nom, gini_cluster, gamma=GAMMA)
    except Exception:
        raw_nom = None
    try:
        raw_ppp = wealth_combined(rank, wealth_percap_ppp, gini_cluster, gamma=GAMMA)
    except Exception:
        raw_ppp = None

    raw_nominal_list.append(raw_nom)
    raw_ppp_list.append(raw_ppp)
    cluster_indices.append(idx)

# Second pass: normalize so country mean equals national GNI (nominal and PPP)
quantiles = [None] * len(clusters_with_both)
income_nom = [None] * len(clusters_with_both)
income_ppp = [None] * len(clusters_with_both)

# Compute normalization factors across all clusters (exclude None)
valid_nom = [v for v in raw_nominal_list if v is not None]
valid_ppp = [v for v in raw_ppp_list if v is not None]

mean_raw_nom = np.nan
mean_raw_ppp = np.nan
if len(valid_nom) > 0:
    mean_raw_nom = float(np.nanmean(valid_nom))
if len(valid_ppp) > 0:
    mean_raw_ppp = float(np.nanmean(valid_ppp))

norm_factor_nom = None
norm_factor_ppp = None
if not np.isnan(national_nominal) and mean_raw_nom and mean_raw_nom > 0:
    norm_factor_nom = national_nominal / mean_raw_nom
if not np.isnan(national_ppp) and mean_raw_ppp and mean_raw_ppp > 0:
    norm_factor_ppp = national_ppp / mean_raw_ppp

# Apply normalization to each computed cluster
for i, original_idx in enumerate(cluster_indices):
    raw_nom = raw_nominal_list[i]
    raw_ppp = raw_ppp_list[i]
    if raw_nom is not None and norm_factor_nom is not None:
        val_nom = raw_nom * norm_factor_nom
        if CAP_MULTIPLE is not None:
            cap_val = national_nominal * CAP_MULTIPLE
            val_nom = min(val_nom, cap_val)
        income_nom[original_idx] = val_nom
    else:
        income_nom[original_idx] = None

    if raw_ppp is not None and norm_factor_ppp is not None:
        val_ppp = raw_ppp * norm_factor_ppp
        if CAP_MULTIPLE is not None:
            cap_val_ppp = national_ppp * CAP_MULTIPLE
            val_ppp = min(val_ppp, cap_val_ppp)
        income_ppp[original_idx] = val_ppp
    else:
        income_ppp[original_idx] = None

    # store quantile
    rank = rwi_to_quantile(clusters_with_both.loc[original_idx, RWI_FIELD])
    quantiles[original_idx] = np.clip(rank, RANK_MIN, RANK_MAX)

clusters_with_both["p"] = quantiles
clusters_with_both["income_nominal_pc"] = income_nom
clusters_with_both["income_ppp_pc"] = income_ppp

# Print summary stats (handle missing values)
nom_ser = pd.to_numeric(clusters_with_both['income_nominal_pc'], errors='coerce')
ppp_ser = pd.to_numeric(clusters_with_both['income_ppp_pc'], errors='coerce')
if nom_ser.notna().any():
    print(f"Income (nominal) range: ${nom_ser.min():.0f} - ${nom_ser.max():.0f}")
    print(f"Mean income (nominal): ${nom_ser.mean():.0f}")
    print(f"Null values (nominal): {nom_ser.isna().sum()}")
else:
    print("Income (nominal): no valid values computed")

if ppp_ser.notna().any():
    print(f"Income (ppp) range: ${ppp_ser.min():.0f} - ${ppp_ser.max():.0f}")
    print(f"Mean income (ppp): ${ppp_ser.mean():.0f}")
    print(f"Null values (ppp): {ppp_ser.isna().sum()}")
else:
    print("Income (ppp): no valid values computed")


# ============================================================
# SAVE OUTPUT
# ============================================================

# Drop geometry column for CSV output
out_dir = os.path.dirname(OUTPUT_CSV)
if out_dir and not os.path.exists(out_dir):
    os.makedirs(out_dir, exist_ok=True)
output_df = pd.DataFrame(clusters_with_both.drop(columns='geometry'))
output_df.to_csv(OUTPUT_CSV, index=False)
print(f"✓ Saved: {OUTPUT_CSV}")
