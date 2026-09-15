"""
Advanced Sample Generator for NAM - Version 2
Creates a combined sample.csv with three types of clusters:
- GHSL: Original population clusters with schools assigned within 250m
- SCHOOL: Standalone school clusters from unassigned schools (clustered if <250m apart)
- SPARSE: Sparse population points from connectivity analysis

Calculates rwi_std from administrative subdivision RWI distribution
"""

import geopandas as gpd
import pandas as pd
import numpy as np
import os
from pathlib import Path
from shapely.geometry import Point
from scipy.spatial.distance import cdist
import rasterio
from rasterio.mask import mask

# ============================================================================
# CONFIGURATION - CHANGE COUNTRY CODE HERE
# ============================================================================
COUNTRY = "MOZ"  # Change this to your country code (e.g., "KEN", "ETH", "ZAF")

# Load HDI values from CSV
HDI_CSV = '/Users/matteo/Desktop/Tesi/Data sheet/hdi_values.csv'
hdi_df = pd.read_csv(HDI_CSV)
hdi_dict = dict(zip(hdi_df['country'], hdi_df['hdi_value']))
HDI = hdi_dict[COUNTRY]

# Derived paths
GHSL_PATH = f'zip:///Users/matteo/Library/CloudStorage/OneDrive-PolitecnicodiMilano/Pieraccini - MGPY-PVGIS/MGPY-JRC Shared Folder/Data/population_cluster_Loads/Clusters_filled_load_2023/GPKG/{COUNTRY}_Population_Clusters_EPSG4326.gpkg.zip!{COUNTRY}_Population_Clusters_EPSG4326.gpkg'
SCHOOLS_PATH = f'/Users/matteo/Desktop/Countries/{COUNTRY}/schools_{COUNTRY}.gpkg'
SPARSE_PATH = f'/Users/matteo/Desktop/Countries/{COUNTRY}/{COUNTRY}_sparse_pop_test_connectivity_8.gpkg'
GADM_PATH = f'/Users/matteo/Desktop/Countries/{COUNTRY}/gadm41_{COUNTRY}.gpkg'
OUTPUT_CSV = f'/Users/matteo/Desktop/Countries/{COUNTRY}/advanced_sample.csv'

# UTM zones for Sub-Saharan African countries (for accurate metric distance calculations)
UTM_ZONES = {
    "AGO": "EPSG:32733", "BDI": "EPSG:32735", "BEN": "EPSG:32631", "BFA": "EPSG:32630",
    "BWA": "EPSG:32734", "CAF": "EPSG:32634", "CIV": "EPSG:32630", "CMR": "EPSG:32632",
    "COD": "EPSG:32734", "COG": "EPSG:32733", "DJI": "EPSG:32638", "ETH": "EPSG:32637",
    "GAB": "EPSG:32732", "GHA": "EPSG:32630", "GIN": "EPSG:32629", "GMB": "EPSG:32628",
    "GNB": "EPSG:32628", "GNQ": "EPSG:32632", "KEN": "EPSG:32637", "LBR": "EPSG:32629",
    "LSO": "EPSG:32735", "MDG": "EPSG:32738", "MLI": "EPSG:32630", "MOZ": "EPSG:32736",
    "MRT": "EPSG:32628", "MWI": "EPSG:32736", "NAM": "EPSG:32733", "NER": "EPSG:32632",
    "NGA": "EPSG:32632", "RWA": "EPSG:32735", "SEN": "EPSG:32628", "SLE": "EPSG:32629",
    "SWZ": "EPSG:32736", "TCD": "EPSG:32634", "TGO": "EPSG:32631", "TZA": "EPSG:32736",
    "UGA": "EPSG:32636", "ZAF": "EPSG:32735", "ZMB": "EPSG:32735", "ZWE": "EPSG:32735"
}

# Climate zone mapping for demand archetypes (numeric to string codes)
COOLING_MAPPING = {
    1: "NC",  # No cooling
    2: "AS",  # Arid/Semi-arid
    3: "OM",  # Other/Moderate
    4: "AY"   # All year cooling
}

# Parameters
SCHOOL_BUFFER_M = 250  # Buffer distance for school assignment to GHSL clusters
SCHOOL_CLUSTER_DISTANCE_M = 250  # Distance threshold for clustering unassigned schools
PROJECTED_CRS = UTM_ZONES.get(COUNTRY, "EPSG:32633")  # Country-specific UTM for metric calculations

# Determine finest administrative level available
def get_finest_admin_level(gadm_path):
    """Find the finest administrative level available"""
    for level in [4, 3, 2, 1]:
        try:
            gpd.read_file(gadm_path, layer=f'ADM_ADM_{level}', rows=1)
            return level
        except:
            continue
    return 1

ADMIN_LEVEL = get_finest_admin_level(GADM_PATH)
print(f"Using administrative level {ADMIN_LEVEL} for rwi_std calculation")
print(f"Country: {COUNTRY} | HDI: {HDI} | CRS: {PROJECTED_CRS}")

# HDI-based household tier thresholds
# Calculate HDI adjustment (as in fillerV5.py)
MIN_HDI = hdi_df['hdi_value'].min()
MAX_HDI = hdi_df['hdi_value'].max()
HDI_ADJUSTMENT = 2 * (HDI - MIN_HDI) / (MAX_HDI - MIN_HDI) - 1

print(f"HDI adjustment: {HDI_ADJUSTMENT:+.3f} (shifts RWI center for tier allocation)")

def allocate_households_by_rwi(rwi_value, rwi_std, total_households, hdi_adjustment):
    """
    Allocate households across 5 tiers based on HDI-adjusted RWI.
    Uses fillerV5.py methodology: HDI shifts RWI center, then allocate to fixed bins.
    
    Args:
        rwi_value: Original cluster RWI value
        rwi_std: RWI standard deviation for this cluster
        total_households: Total number of households to allocate
        hdi_adjustment: HDI-based shift (-1 to +1)
    
    Returns:
        List of 5 integers [h_tier1, h_tier2, h_tier3, h_tier4, h_tier5]
    """
    # Apply HDI adjustment to RWI center
    adjusted_center = rwi_value + hdi_adjustment
    
    # Use minimum std to avoid extremely narrow distributions
    std = max(rwi_std, 0.1)
    
    # Fixed RWI bins for tiers (as in fillerV5)
    bins = [-2.5, -1.5, -0.5, 0.5, 1.5, 2.5]
    x = np.linspace(-3.0, 3.0, 1000)
    
    # Generate Gaussian PDF around adjusted center
    pdf = np.exp(-0.5 * ((x - adjusted_center)/std)**2)
    pdf /= pdf.sum()
    
    # Calculate probability for each bin
    counts = []
    for i in range(5):
        idx = (x >= bins[i]) & (x < bins[i+1])
        fraction = pdf[idx].sum()
        counts.append(int(round(fraction * total_households)))
    
    return counts

def sample_raster_at_points(raster_path, gdf):
    """Sample raster values at point locations"""
    with rasterio.open(raster_path) as src:
        # Ensure GDF is in same CRS as raster
        if gdf.crs != src.crs:
            gdf = gdf.to_crs(src.crs)
        
        # Extract coordinates
        coords = [(geom.x, geom.y) for geom in gdf.geometry]
        
        # Sample raster at coordinates
        sampled = list(src.sample(coords))
        
        # Process values (handle nodata)
        values = []
        for val in sampled:
            extracted = val[0]
            if extracted == src.nodata or extracted is None:
                values.append(np.nan)
            else:
                values.append(float(extracted))
        
        return values

def calculate_rwi_std_from_admin(gdf, admin_gdf):
    """
    Calculate RWI standard deviation for each geometry based on
    the RWI distribution of ALL clusters within its administrative subdivision.
    
    This follows the original methodology: std of cluster RWI values within admin units,
    not std of raster pixels.
    """
    # Determine admin ID column
    admin_id_col = f'GID_{ADMIN_LEVEL}'
    
    # Spatial join to assign admin subdivision to each geometry
    gdf_with_admin = gpd.sjoin(gdf, admin_gdf[[admin_id_col, 'geometry']], how='left', predicate='within')
    
    # Calculate overall std as fallback (calculated BEFORE grouping)
    overall_std = float(gdf['rwi'].std()) if len(gdf) > 1 else 0.0
    
    # Calculate RWI std dev per administrative unit from cluster RWI values
    def calculate_std(group):
        valid_rwi = group['rwi'].dropna()
        if len(valid_rwi) <= 1:
            return np.nan  # Return NaN for single-cluster units (will be filled with overall_std)
        std_val = float(valid_rwi.std())
        if std_val == 0.0:
            return np.nan  # Return NaN when all values are identical (will be filled with overall_std)
        return std_val
    
    std_df = gdf_with_admin.groupby(admin_id_col).apply(calculate_std).reset_index()
    std_df.columns = [admin_id_col, 'rwi_std_calc']
    
    # Merge back to get std for each cluster
    gdf_with_std = gdf_with_admin.merge(std_df, on=admin_id_col, how='left')
    
    # For units without std dev (single cluster or no assignment), use overall std
    rwi_std_values = gdf_with_std['rwi_std_calc'].fillna(overall_std).tolist()
    
    return rwi_std_values

# ============================================================================
# PART 1: PROCESS GHSL CLUSTERS
# ============================================================================
print("="*80)
print(f"PROCESSING {COUNTRY} - PART 1: GHSL CLUSTERS")
print("="*80)

# Load GHSL clusters
ghsl = gpd.read_file(GHSL_PATH, layer=f'{COUNTRY}_population_clusters_poly')
print(f"Loaded {len(ghsl)} GHSL clusters")

# Load administrative boundaries
admin_gdf = gpd.read_file(GADM_PATH, layer=f'ADM_ADM_{ADMIN_LEVEL}')
print(f"Loaded {len(admin_gdf)} administrative subdivisions (level {ADMIN_LEVEL})")

# Note: rwi_std will be calculated later after loading sparse clusters
# so we can combine GHSL + SPARSE for joint calculation

# Load schools
schools = gpd.read_file(SCHOOLS_PATH)
print(f"Loaded {len(schools)} schools")

# Convert to projected CRS for accurate distance calculations
ghsl_proj = ghsl.to_crs(PROJECTED_CRS)
schools_proj = schools.to_crs(PROJECTED_CRS)

# Create 250m buffer around GHSL clusters for school assignment
ghsl_buffered = ghsl_proj.copy()
ghsl_buffered['geometry'] = ghsl_proj.geometry.buffer(SCHOOL_BUFFER_M)
ghsl_buffered['cat'] = ghsl['cat']  # Preserve original cat IDs

# Spatial join: assign schools to buffered GHSL clusters
schools_assigned = gpd.sjoin(schools_proj, ghsl_buffered[['cat', 'geometry']], how='left', predicate='within')

# Find which original schools were assigned vs unassigned
# Group by original index to see if each school got at least one assignment
assigned_indices = schools_assigned[schools_assigned['cat'].notna()].index.unique()
unassigned_indices = schools_assigned[schools_assigned['cat'].isna()].index.unique()

# Get the actual school records
assigned_schools = schools_assigned[schools_assigned.index.isin(assigned_indices)].copy()
unassigned_schools = schools_proj.loc[unassigned_indices].copy()

print(f"Schools assigned to GHSL clusters: {len(assigned_indices)}")
print(f"Unassigned schools: {len(unassigned_indices)}")

# Aggregate school demand by GHSL cluster
school_demand_by_cluster = assigned_schools.groupby('cat')['dem_el_wfi'].agg(['sum', 'count']).reset_index()
school_demand_by_cluster.columns = ['cat', 'school_total_demand', 'schools']

# Hospitals are already assigned in the GHSL source data
# Extract hospital counts from GHSL attributes
hospital_columns = ['health_first', 'health_primary', 'health_secondary', 'health_tertiary']
if all(col in ghsl.columns for col in hospital_columns):
    hospital_pivot = ghsl[['cat'] + hospital_columns].copy()
    # Map to hospital_1 through hospital_4 (tier 5 doesn't exist in data)
    hospital_pivot.columns = ['cat', 'hospital_1', 'hospital_2', 'hospital_3', 'hospital_4']
    hospital_pivot['hospital_5'] = 0
    print(f"Extracted hospital data from GHSL attributes")
else:
    # Fallback: no hospitals
    hospital_pivot = pd.DataFrame({'cat': ghsl['cat']})
    for i in range(1, 6):
        hospital_pivot[f'hospital_{i}'] = 0
    print("No hospital data found in GHSL attributes")

# Create GHSL sample dataframe
ghsl_sample = []

for idx, row in ghsl.iterrows():
    cat = row['cat']
    
    # Get school data
    school_data = school_demand_by_cluster[school_demand_by_cluster['cat'] == cat]
    if len(school_data) > 0:
        schools_count = int(school_data['schools'].iloc[0])
        # Convert from kW to W (multiply by 1000)
        school_demand = float(school_data['school_total_demand'].iloc[0]) * 1000
    else:
        schools_count = 0
        school_demand = 0.0
    
    # Get hospital data
    hosp_data = hospital_pivot[hospital_pivot['cat'] == cat]
    if len(hosp_data) > 0:
        h1 = int(hosp_data['hospital_1'].fillna(0).iloc[0])
        h2 = int(hosp_data['hospital_2'].fillna(0).iloc[0])
        h3 = int(hosp_data['hospital_3'].fillna(0).iloc[0])
        h4 = int(hosp_data['hospital_4'].fillna(0).iloc[0])
        h5 = int(hosp_data['hospital_5'].fillna(0).iloc[0])
    else:
        h1 = h2 = h3 = h4 = h5 = 0
    
    # Allocate households based on HDI-adjusted RWI (fillerV5 methodology)
    total_pop = row['POP_sum']
    rwi_value = row['rwi']
    rwi_std_value = row['rwi_std'] if 'rwi_std' in row and pd.notna(row['rwi_std']) else 0.3
    
    # Calculate households per tier using HDI-adjusted RWI
    total_households = total_pop / 5
    tier_counts = allocate_households_by_rwi(rwi_value, rwi_std_value, total_households, HDI_ADJUSTMENT)
    h_tier1, h_tier2, h_tier3, h_tier4, h_tier5 = tier_counts
    
    # Get centroid coordinates
    centroid = row.geometry.centroid
    
    ghsl_sample.append({
        'cat': f'GHSL_{int(cat)}',
        'h_tier1': h_tier1,
        'h_tier2': h_tier2,
        'h_tier3': h_tier3,
        'h_tier4': h_tier4,
        'h_tier5': h_tier5,
        'hospital_1': h1,
        'hospital_2': h2,
        'hospital_3': h3,
        'hospital_4': h4,
        'hospital_5': h5,
        'schools': schools_count,
        'school_total_demand': school_demand,
        'lat': centroid.y,
        'lon': centroid.x,
        'distance_grid_km': row['distance_grid_km'],
        'pop_sum': row['POP_sum'],
        'density': row['DEN_average'],
        'cooling': COOLING_MAPPING.get(row['climate_class'], 'NA'),
        'rwi': row['rwi'],
        'rwi_std': row.get('rwi_std', 0.0)  # Will be updated after sparse cluster processing
    })

ghsl_sample_temp = ghsl_sample  # Store temporarily, will create dataframe after rwi_std calculation

print(f"\nCreated {len(ghsl_sample_temp)} GHSL cluster records (rwi_std will be calculated after sparse processing)")

# ============================================================================
# PART 2: CREATE SCHOOL CLUSTERS
# ============================================================================
print("\n" + "="*80)
print(f"PART 2: CREATING SCHOOL CLUSTERS")
print("="*80)

if len(unassigned_schools) > 0:
    # Extract coordinates in projected CRS (meters)
    coords_m = np.array([[geom.x, geom.y] for geom in unassigned_schools.geometry])
    
    # Simple distance-based clustering
    n_schools = len(coords_m)
    cluster_labels = np.arange(n_schools)
    
    # Calculate pairwise distances
    distances = cdist(coords_m, coords_m)
    
    # Iteratively merge clusters if schools are within threshold
    for i in range(n_schools):
        for j in range(i + 1, n_schools):
            if distances[i, j] <= SCHOOL_CLUSTER_DISTANCE_M:
                old_label = cluster_labels[j]
                cluster_labels[cluster_labels == old_label] = cluster_labels[i]
    
    # Renumber clusters consecutively
    unique_labels = np.unique(cluster_labels)
    label_map = {old: new for new, old in enumerate(unique_labels)}
    cluster_labels = np.array([label_map[label] for label in cluster_labels])
    
    unassigned_schools['cluster_id'] = cluster_labels
    
    print(f"Created {len(np.unique(cluster_labels))} school clusters")
    
    # Aggregate by cluster
    school_clusters = []
    for cluster_id in np.unique(cluster_labels):
        cluster_schools = unassigned_schools[unassigned_schools['cluster_id'] == cluster_id]
        
        # Calculate centroid in projected CRS, then convert to lat/lon
        centroid_proj = cluster_schools.geometry.union_all().centroid
        centroid_gdf = gpd.GeoSeries([centroid_proj], crs=PROJECTED_CRS).to_crs('EPSG:4326')
        centroid = centroid_gdf[0]
        
        # Sum demand and convert from kW to W (multiply by 1000)
        total_demand = cluster_schools['dem_el_wfi'].sum() * 1000
        num_schools = len(cluster_schools)
        
        school_clusters.append({
            'cat': f'SCHOOL_{cluster_id + 1}',
            'h_tier1': 0,
            'h_tier2': 0,
            'h_tier3': 0,
            'h_tier4': 0,
            'h_tier5': 0,
            'hospital_1': 0,
            'hospital_2': 0,
            'hospital_3': 0,
            'hospital_4': 0,
            'hospital_5': 0,
            'schools': num_schools,
            'school_total_demand': total_demand,
            'lat': centroid.y,
            'lon': centroid.x,
            'distance_grid_km': 0.0,
            'pop_sum': 0.0,
            'density': 0.0,
            'cooling': 'NA',
            'rwi': 0.0,
            'rwi_std': 0.0
        })
    
    school_df = pd.DataFrame(school_clusters)
    print(f"Created {len(school_df)} SCHOOL cluster records")
else:
    # Need to create empty dataframe with same columns as will be in ghsl_df
    school_df = pd.DataFrame(columns=[
        'cat', 'h_tier1', 'h_tier2', 'h_tier3', 'h_tier4', 'h_tier5',
        'hospital_1', 'hospital_2', 'hospital_3', 'hospital_4', 'hospital_5',
        'schools', 'school_total_demand', 'lat', 'lon', 
        'distance_grid_km', 'pop_sum', 'density', 'cooling', 'rwi', 'rwi_std'
    ])
    print("No unassigned schools - no SCHOOL clusters created")

# ============================================================================
# PART 3: PROCESS SPARSE CLUSTERS
# ============================================================================
print("\n" + "="*80)
print(f"PART 3: PROCESSING SPARSE CLUSTERS")
print("="*80)

# Load sparse population points
# Extract layer name from path for explicit layer specification
sparse_layer = Path(SPARSE_PATH).stem  # Gets filename without extension
sparse = gpd.read_file(SPARSE_PATH, layer=sparse_layer)
print(f"Loaded {len(sparse)} sparse population points")

# Ensure same CRS
if sparse.crs != 'EPSG:4326':
    sparse = sparse.to_crs('EPSG:4326')

# RWI values already in sparse GPKG (from sparse_conectivity.py)
print("Reading RWI values from sparse cluster GPKG...")
if 'rwi' not in sparse.columns:
    raise ValueError("ERROR: 'rwi' column not found in sparse GPKG. Run sparse_conectivity.py first!")

# Validate RWI values
valid_rwi_count = sparse['rwi'].notna().sum()
print(f"Valid RWI values: {valid_rwi_count} / {len(sparse)}")
if valid_rwi_count == 0:
    raise ValueError("ERROR: No valid RWI values in sparse GPKG!")

# Calculate rwi_std combining GHSL and SPARSE clusters for admin unit calculation
print("Calculating rwi_std from administrative subdivision RWI distribution...")
# Combine GHSL and SPARSE for joint calculation
combined_for_std = pd.concat([
    ghsl[['geometry', 'rwi']],
    sparse[['geometry', 'rwi']]
], ignore_index=True)
combined_gdf = gpd.GeoDataFrame(combined_for_std, geometry='geometry', crs='EPSG:4326')

# Calculate std for combined dataset
all_rwi_std = calculate_rwi_std_from_admin(combined_gdf, admin_gdf)

# Split back to GHSL and SPARSE
ghsl['rwi_std'] = all_rwi_std[:len(ghsl)]
sparse['rwi_std'] = all_rwi_std[len(ghsl):]

print(f"rwi_std calculated from combined GHSL+SPARSE distribution")
print(f"  GHSL rwi_std mean: {ghsl['rwi_std'].mean():.3f}")
print(f"  SPARSE rwi_std mean: {sparse['rwi_std'].mean():.3f}")

# For cooling class, find closest GHSL cluster
ghsl_proj_centroids = ghsl.to_crs(PROJECTED_CRS).copy()
ghsl_proj_centroids['geometry'] = ghsl_proj_centroids.geometry.centroid
sparse_proj = sparse.to_crs(PROJECTED_CRS)

sparse_sample = []

for idx, row in sparse_proj.iterrows():
    # Find closest GHSL cluster for cooling class
    distances = ghsl_proj_centroids.geometry.distance(row.geometry)
    closest_idx = distances.idxmin()
    closest_ghsl = ghsl_proj_centroids.loc[closest_idx]
    
    # Get lat/lon from original sparse data
    original_geom = sparse.loc[idx, 'geometry']
    
    # Get population and density
    pop = row['POP']
    density = row['DEN']
    
    # Get RWI and rwi_std (already calculated)
    rwi = sparse.loc[idx, 'rwi']
    rwi_std = sparse.loc[idx, 'rwi_std']
    
    # Allocate households based on HDI-adjusted RWI (fillerV5 methodology)
    total_households = pop / 5
    tier_counts = allocate_households_by_rwi(rwi, rwi_std, total_households, HDI_ADJUSTMENT)
    h_tier1, h_tier2, h_tier3, h_tier4, h_tier5 = tier_counts
    
    sparse_sample.append({
        'cat': f'SPARSE_{idx + 1}',
        'h_tier1': h_tier1,
        'h_tier2': h_tier2,
        'h_tier3': h_tier3,
        'h_tier4': h_tier4,
        'h_tier5': h_tier5,
        'hospital_1': 0,
        'hospital_2': 0,
        'hospital_3': 0,
        'hospital_4': 0,
        'hospital_5': 0,
        'schools': 0,
        'school_total_demand': 0.0,
        'lat': original_geom.y,
        'lon': original_geom.x,
        'distance_grid_km': 0.0,
        'pop_sum': pop,
        'density': density,
        'cooling': COOLING_MAPPING.get(closest_ghsl['climate_class'], 'NA'),
        'rwi': rwi,
        'rwi_std': rwi_std
    })

sparse_df = pd.DataFrame(sparse_sample)
print(f"Created {len(sparse_df)} SPARSE cluster records")

# Now create GHSL dataframe with updated rwi_std values
print("\nUpdating GHSL dataframe with calculated rwi_std...")
for record in ghsl_sample_temp:
    cat_num = int(record['cat'].replace('GHSL_', ''))
    ghsl_row = ghsl[ghsl['cat'] == cat_num]
    if len(ghsl_row) > 0:
        record['rwi_std'] = ghsl_row['rwi_std'].values[0]

ghsl_df = pd.DataFrame(ghsl_sample_temp)
print(f"GHSL dataframe ready: {len(ghsl_df)} clusters (rwi_std mean: {ghsl_df['rwi_std'].mean():.3f})")

# ============================================================================
# PART 4: COMBINE AND SAVE
# ============================================================================
print("\n" + "="*80)
print(f"PART 4: COMBINING AND SAVING")
print("="*80)

# Combine all three dataframes
combined_df = pd.concat([ghsl_df, school_df, sparse_df], ignore_index=True)

# Reorder columns to match standard format
column_order = [
    'cat', 'h_tier1', 'h_tier2', 'h_tier3', 'h_tier4', 'h_tier5',
    'hospital_1', 'hospital_2', 'hospital_3', 'hospital_4', 'hospital_5',
    'schools', 'school_total_demand', 'lat', 'lon', 
    'distance_grid_km', 'pop_sum', 'density', 'cooling', 'rwi', 'rwi_std'
]

combined_df = combined_df[column_order]

# Save to CSV
output_path = Path(OUTPUT_CSV)
output_path.parent.mkdir(parents=True, exist_ok=True)
combined_df.to_csv(output_path, index=False)

print(f"\n✅ {COUNTRY} - Successfully created advanced_sample.csv")
print(f"   Output: {output_path}")
print(f"\nSummary:")
print(f"   GHSL clusters:   {len(ghsl_df)} (rwi_std mean: {ghsl_df['rwi_std'].mean():.3f})")
print(f"   SCHOOL clusters: {len(school_df)}")
print(f"   SPARSE clusters: {len(sparse_df)} (rwi_std mean: {sparse_df['rwi_std'].mean():.3f})")
print(f"   TOTAL:           {len(combined_df)}")
