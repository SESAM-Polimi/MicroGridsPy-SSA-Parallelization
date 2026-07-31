import geopandas as gpd
import rasterio
from rasterio import features
import numpy as np
import os

# =============================================================================
# CONFIGURATION - CHANGE COUNTRY CODE HERE
# =============================================================================
COUNTRY = "ZWE"  # Change this to your country code (e.g., "KEN", "ETH", "ZAF")

# Derived paths
raster_path = f'/Users/matteo/Desktop/Countries/{COUNTRY}/{COUNTRY.lower()}_pop_2020_CN_1km_R2025A_UA_v1.tif'
polygon_path = f'zip:///Users/matteo/Library/CloudStorage/OneDrive-PolitecnicodiMilano/Pieraccini - MGPY-PVGIS/MGPY-JRC Shared Folder/Data/population_cluster_Loads/Clusters_filled_load_2023/GPKG/{COUNTRY}_Population_Clusters_EPSG4326.gpkg.zip!{COUNTRY}_Population_Clusters_EPSG4326.gpkg'
polygon_layer = f"{COUNTRY}_population_clusters_poly"
rwi_raster_path = '/Users/matteo/Library/CloudStorage/OneDrive-PolitecnicodiMilano/Pieraccini - MGPY-PVGIS/MGPY-JRC Shared Folder/Data/Wealth_Human Development_Deprivation/Relative_Wealth_Index/rwi.tif'
gadm_path = f'/Users/matteo/Desktop/Countries/{COUNTRY}/gadm41_{COUNTRY}.gpkg'
output_raster_step1 = f"/Users/matteo/Desktop/Countries/{COUNTRY}/{COUNTRY.lower()}_sparse.tif"
output_raster_step2 = f"/Users/matteo/Desktop/Countries/{COUNTRY}/{COUNTRY.lower()}_sparse_filtered.tif"
output_gpkg_final = f"/Users/matteo/Desktop/Countries/{COUNTRY}/{COUNTRY}_sparse_pop_test_connectivity_8.gpkg"

# Parameters
POPULATION_THRESHOLD = 300
CONNECTIVITY = 8  # 8, 4, or 1

# =============================================================================
# STEP 1: EXTRACT SPARSE POPULATION (outside GHSL clusters)
# =============================================================================
print("="*80)
print(f"PROCESSING {COUNTRY} - STEP 1: EXTRACTING SPARSE POPULATION")
print("="*80)

# 1. Load the polygon
gdf = gpd.read_file(polygon_path, layer=polygon_layer)
print(f"Loaded {len(gdf)} GHSL clusters")

# Dissolve multiple features into one mask (recommended)
gdf = gdf.dissolve()

# 2. Open raster
with rasterio.open(raster_path) as src:
    raster = src.read(1)
    out_meta = src.meta.copy()

    # Reproject polygon to raster CRS if needed
    if gdf.crs != src.crs:
        gdf = gdf.to_crs(src.crs)

    # 3. Create mask raster (1 = inside polygon, 0 = outside)
    # all_touched=True → any pixel touched by polygon boundary is included
    mask_inside = features.geometry_mask(
        gdf.geometry,
        out_shape=src.shape,
        transform=src.transform,
        invert=True,  # True → inside geometry = True
        all_touched=True  # Include pixels that are even partially touched
    )

    # Convert boolean mask to integers (1/0)
    mask_inside = mask_inside.astype(np.uint8)

    # 4. Invert mask → 1 = OUTSIDE the polygon
    mask_outside = 1 - mask_inside

    # 5. Apply mask to raster
    raster_outside = raster * mask_outside
    raster_outside = np.where(mask_outside == 1, raster, src.nodata or 0)

# 6. Save output raster
with rasterio.open(output_raster_step1, "w", **out_meta) as dst:
    dst.write(raster_outside, 1)

print(f"✓ Saved sparse population raster: {output_raster_step1}\n")

# =============================================================================
# STEP 2: FILTER POPULATION THRESHOLD (>= 300)
# =============================================================================
print("="*80)
print(f"STEP 2: FILTERING POPULATION THRESHOLD (>= {POPULATION_THRESHOLD})")
print("="*80)

input_raster_step2 = output_raster_step1

# Open the raster
with rasterio.open(input_raster_step2) as src:
    profile = src.profile  # keep metadata
    data = src.read(1)     # read first band

# Filter values < threshold
data_filtered = np.where(data >= POPULATION_THRESHOLD, data, profile['nodata'] or np.nan)

# Update profile to ensure nodata is set
profile.update(
    dtype=rasterio.float32,
    nodata=profile['nodata'] or np.nan
)

# Write filtered raster
with rasterio.open(output_raster_step2, 'w', **profile) as dst:
    dst.write(data_filtered.astype(rasterio.float32), 1)

print(f"✓ Filtered raster saved: {output_raster_step2}\n")

from scipy.ndimage import label
from shapely.geometry import Point

# =============================================================================
# STEP 3: CREATE CONNECTIVITY CLUSTERS (8-connectivity)
# =============================================================================
print("="*80)
print(f"STEP 3: CREATING CONNECTIVITY CLUSTERS ({CONNECTIVITY}-connectivity)")
print("="*80)

input_raster_step3 = output_raster_step2

# Choose connectivity: 8, 4, or 1
connectivity = CONNECTIVITY

# ---------------------------
# Step 1: Read raster
# ---------------------------
with rasterio.open(input_raster_step3) as src:
    data = src.read(1)
    transform = src.transform
    crs = src.crs
    nodata = src.nodata

# ---------------------------
# Step 2: Valid mask
# ---------------------------
if nodata is not None:
    valid_mask = data != nodata
else:
    valid_mask = ~np.isnan(data)

# ---------------------------
# Step 3: Define connectivity structure
# ---------------------------
if connectivity == 8:
    structure = np.ones((3,3), dtype=int)  # 8-connectivity
elif connectivity == 4:
    structure = np.array([[0,1,0],
                          [1,1,1],
                          [0,1,0]], dtype=int)  # 4-connectivity
elif connectivity == 1:
    structure = np.array([[0,0,0],
                          [0,1,0],
                          [0,0,0]], dtype=int)  # single-tile clusters
else:
    raise ValueError("connectivity must be 1, 4, or 8")

# ---------------------------
# Step 4: Label clusters
# ---------------------------
labeled_array, num_features = label(valid_mask, structure=structure)
print(f"Number of clusters found: {num_features}")

# ---------------------------
# Step 5: Compute cluster stats
# ---------------------------
cluster_ids = np.arange(1, num_features+1)
points = []
pop_list = []
den_list = []
x_coords = []
y_coords = []

for cluster_id in cluster_ids:
    indices = np.where(labeled_array == cluster_id)
    if len(indices[0]) == 0:
        continue

    cluster_values = data[indices]
    pop = np.sum(cluster_values)
    den = pop / len(cluster_values)

    row_mean = np.mean(indices[0])
    col_mean = np.mean(indices[1])
    cx, cy = rasterio.transform.xy(transform, row_mean, col_mean, offset='center')

    points.append(Point(cx, cy))
    pop_list.append(pop)
    den_list.append(den)
    x_coords.append(cx)
    y_coords.append(cy)

# ---------------------------
# Step 6: Create GeoDataFrame
# ---------------------------
gdf = gpd.GeoDataFrame({
    'POP': pop_list,
    'DEN': den_list,
    'x': x_coords,
    'y': y_coords
}, geometry=points, crs=crs)

gdf = gdf.to_crs(epsg=4326)

# =============================================================================
# STEP 4: SAMPLE RWI VALUES AT SPARSE CLUSTER POINTS
# =============================================================================
print("\n" + "="*80)
print(f"STEP 4: SAMPLING RWI VALUES AT SPARSE CLUSTER POINTS")
print("="*80)

# Open RWI raster and sample at point locations (memory-efficient approach)
with rasterio.open(rwi_raster_path) as rwi_src:
    # Ensure GDF is in same CRS as RWI raster
    gdf_rwi_crs = gdf.to_crs(rwi_src.crs) if gdf.crs != rwi_src.crs else gdf
    
    # Sample raster at coordinates (iterator approach to avoid loading entire raster)
    rwi_values = []
    for i, (idx, geom) in enumerate(zip(gdf_rwi_crs.index, gdf_rwi_crs.geometry)):
        # Use iterator approach to avoid buffering all samples
        try:
            # Sample single point (generator returns iterator)
            sample_iter = rwi_src.sample([(geom.x, geom.y)])
            extracted = next(sample_iter)[0]
            
            if extracted == rwi_src.nodata or np.isnan(extracted):
                rwi_values.append(np.nan)
            else:
                rwi_values.append(float(extracted))
        except (StopIteration, IndexError):
            rwi_values.append(np.nan)
        
        # Progress indicator every 500 points
        if (i + 1) % 500 == 0:
            print(f"  Sampled {i + 1}/{len(gdf_rwi_crs)} points")

# Add RWI column to GeoDataFrame
gdf['rwi'] = rwi_values

# Fill NaN values with mean of valid RWI values within same administrative region
nan_count = gdf['rwi'].isna().sum()
if nan_count > 0:
    print(f"\nFilling {nan_count} NaN RWI values using administrative regions...")
    
    # Determine finest admin level available
    admin_level = None
    for level in [3, 2, 1, 0]:
        try:
            gpd.read_file(gadm_path, layer=f'ADM_ADM_{level}', rows=1)
            admin_level = level
            break
        except:
            continue
    
    if admin_level is not None:
        # Load admin boundaries
        admin_gdf = gpd.read_file(gadm_path, layer=f'ADM_ADM_{admin_level}')
        admin_id_col = f'GID_{admin_level}'
        print(f"  Using admin level {admin_level} ({len(admin_gdf)} regions)")
        
        # Spatial join: assign clusters to admin regions
        gdf_with_admin = gpd.sjoin(gdf, admin_gdf[[admin_id_col, 'geometry']], how='left', predicate='within')
        
        # Calculate mean RWI per admin region (from valid values)
        admin_means = gdf_with_admin[gdf_with_admin['rwi'].notna()].groupby(admin_id_col)['rwi'].mean()
        
        # Fill NaN values with admin region mean
        filled_count = 0
        country_mean = gdf['rwi'].mean()  # Fallback for regions with no valid RWI
        
        for idx, row in gdf.iterrows():
            if np.isnan(row['rwi']):
                # Get admin region for this cluster
                admin_id = gdf_with_admin.loc[idx, admin_id_col] if idx in gdf_with_admin.index else None
                
                if admin_id is not None and admin_id in admin_means.index:
                    # Use admin region mean
                    gdf.at[idx, 'rwi'] = admin_means[admin_id]
                    filled_count += 1
                elif not np.isnan(country_mean):
                    # Fallback to country mean if admin region has no valid RWI
                    gdf.at[idx, 'rwi'] = country_mean
                    filled_count += 1
        
        print(f"  ✓ Filled {filled_count} NaN values with admin region means")
    else:
        # Fallback: use country-wide mean if no admin boundaries available
        country_mean = gdf['rwi'].mean()
        if not np.isnan(country_mean):
            gdf['rwi'] = gdf['rwi'].fillna(country_mean)
            print(f"  ✓ Filled {nan_count} NaN values with country mean: {country_mean:.3f}")
        else:
            print(f"  ⚠ Warning: Cannot fill NaN - no valid RWI values")

# Save to GPKG
gdf.to_file(output_gpkg_final, driver='GPKG')

print(f"\n✓ {len(points)} sparse cluster points saved with RWI: {output_gpkg_final}")
print("\n" + "="*80)
print(f"✅ {COUNTRY} - ALL STEPS COMPLETED SUCCESSFULLY")
print("="*80)