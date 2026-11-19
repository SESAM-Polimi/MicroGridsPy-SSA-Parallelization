import pandas as pd
import geopandas as gpd
from pathlib import Path
from shapely.geometry import Point
from scipy.spatial import Voronoi
import numpy as np
import matplotlib.pyplot as plt

# ============================================================================
# CONFIGURATION
# ============================================================================

# Input/Output paths - EDIT THESE AS NEEDED
# Make `COUNTRY` an easy variable to change. Default set to 'AGO' for testing.
COUNTRY = 'ZAF'  # ISO3 country code (e.g. 'CMR' for Cameroon)
INPUT_CSV = Path(f'/Volumes/TOSHIBA EXT/OUTPUTS COUNTRY/{COUNTRY}_sample.csv')
# Fallback: try alternate locations if main path doesn't exist
if not INPUT_CSV.exists():
    alt_dir = Path('/Volumes/TOSHIBA EXT/OUTPUTS COUNTRY')
    if alt_dir.exists():
        candidate = alt_dir / f'{COUNTRY}_sample.csv'
        if candidate.exists():
            INPUT_CSV = candidate
        else:
            found = list(alt_dir.glob('*_sample.csv'))
            if found:
                pref = [p for p in found if p.name.startswith(f'{COUNTRY}_')]
                INPUT_CSV = pref[0] if pref else found[0]
COUNTRY_BOUNDARY = Path(f'/Users/matteo/Desktop/Tesi/GADM/gadm41_{COUNTRY}.gpkg')
POWER_LINES = Path('/Users/matteo/Desktop/Tesi/qgis/electricitygrid_Africa_JRC/elect_grid_africa_epsg3426_withgau_JRC.shp')
OUTPUT_DIR = Path(f'/Volumes/TOSHIBA EXT/map/{COUNTRY}/grid_connection')  # Dynamic output directory by country

LON_COLUMN = 'lon'
LAT_COLUMN = 'lat'
OUTPUT_CRS = 'EPSG:4326'

# Weight scenarios for grid connection indicator
SCENARIOS = {
    'conservative': {
        'description': 'Economical - strict on distance and demand',
        'weights': {
            'distance_grid_km': 0.40,
            'demand_year1_kwh': 0.30,
            'pop_sum': 0.10,
            'density': 0.10,
            'rwi': 0.10
        }
    },
    'demand_focused': {
        'description': 'Energy demand driven',
        'weights': {
            'distance_grid_km': 0.20,
            'demand_year1_kwh': 0.45,
            'pop_sum': 0.125,
            'density': 0.125,
            'rwi': 0.10
        }
    },
    'people_focused': {
        'description': 'Equity - population and wealth focused',
        'weights': {
            'distance_grid_km': 0.05,
            'demand_year1_kwh': 0.10,
            'pop_sum': 0.35,
            'density': 0.35,
            'rwi': 0.15
        }
    }
}

# Bin thresholds for classification (red/yellow/green)
BIN_THRESHOLDS = [0, 0.25, 0.50, 1.0]

# Distance penalty/bonus configuration
DISTANCE_BONUS_THRESHOLD = 10  # km - clusters closer get bonus
DISTANCE_PENALTY_THRESHOLD = 150  # km - clusters farther get penalty
BONUS_AMOUNT = 8  # points added
PENALTY_AMOUNT = 8  # points subtracted

# Color scheme
COLOR_SCHEME = {
    'red_bright': '#FF0000',
    'red_soft': '#FFB3B3',
    'yellow_bright': '#FFFF00',
    'yellow_soft': '#FFFFE0',
    'green_bright': '#00CC00',
    'green_soft': '#B3FFB3'
}

# ============================================================================
# FUNCTIONS
# ============================================================================

def normalize_to_100(series, direction='higher_better'):
    """Normalize series to 0-100 range."""
    min_val = series.min()
    max_val = series.max()

    if max_val == min_val:
        return pd.Series([50.0] * len(series), index=series.index)
    
    normalised = (series - min_val) / (max_val - min_val)
    
    if direction == 'lower_better':
        normalised = 1 - normalised
    
    return normalised * 100


def calculate_indicators(df, scenarios):
    """Calculate weighted indicators for all scenarios."""
    result_df = df.copy()
    
    # Normalize all parameters
    parameters_config = {
        'pop_sum': 'higher_better',
        'distance_grid_km': 'lower_better',
        'rwi': 'higher_better',
        'density': 'higher_better',
        'demand_year1_kwh': 'higher_better'
    }
    
    print("\nNormalizing parameters:")
    print("-" * 60)
    
    for param_name, direction in parameters_config.items():
        normalised_col = f'{param_name}_norm'
        result_df[normalised_col] = normalize_to_100(df[param_name], direction)
        print(f"{param_name:20s} | Range: [{result_df[normalised_col].min():.2f}, {result_df[normalised_col].max():.2f}]")
    
    # Calculate weighted index for each scenario
    print("\n" + "=" * 60)
    print("CALCULATING WEIGHTED INDICES")
    print("=" * 60)
    
    for scenario_name, scenario_config in scenarios.items():
        weights = scenario_config['weights']
        weighted_index = pd.Series([0.0] * len(df), index=df.index)
        
        print(f"\n{scenario_name.upper()}: {scenario_config['description']}")
        print("-" * 60)
        
        for param_name, weight in weights.items():
            normalised_col = f'{param_name}_norm'
            weighted_index += result_df[normalised_col] * weight
            print(f"  {param_name:20s}: {weight:5.1%}")
        
        # Apply distance penalty/bonus
        distance_adj = pd.Series(0.0, index=result_df.index)
        close_mask = result_df['distance_grid_km'] < DISTANCE_BONUS_THRESHOLD
        far_mask = result_df['distance_grid_km'] > DISTANCE_PENALTY_THRESHOLD
        distance_adj[close_mask] = BONUS_AMOUNT
        distance_adj[far_mask] = -PENALTY_AMOUNT
        weighted_index = weighted_index + distance_adj
        
        # Normalize weighted index to 0-1 range
        result_df[f'{scenario_name}_index'] = np.clip(weighted_index / 100.0, 0, 1)
        
        # Create bins
        result_df[f'{scenario_name}_bin'] = pd.cut(
            result_df[f'{scenario_name}_index'],
            bins=BIN_THRESHOLDS,
            labels=['red', 'yellow', 'green'],
            include_lowest=True
        )
        
        print(f"  Index range: [{result_df[f'{scenario_name}_index'].min():.4f}, {result_df[f'{scenario_name}_index'].max():.4f}]")
        print(f"  Distribution:")
        print(f"    Red (0-{BIN_THRESHOLDS[1]}):      {(result_df[f'{scenario_name}_bin'] == 'red').sum():3d} clusters")
        print(f"    Yellow ({BIN_THRESHOLDS[1]}-{BIN_THRESHOLDS[2]}): {(result_df[f'{scenario_name}_bin'] == 'yellow').sum():3d} clusters")
        print(f"    Green ({BIN_THRESHOLDS[2]}-1.0):  {(result_df[f'{scenario_name}_bin'] == 'green').sum():3d} clusters")
    
    return result_df


def create_voronoi_geodataframe(gdf, scenario_name, country_boundary):
    """Create Voronoi diagram from village points and clip to country boundary."""
    print(f"\nCreating Voronoi tessellation for {scenario_name}...")
    
    # Get coordinates
    coords = np.array([[p.x, p.y] for p in gdf.geometry])
    
    # Create Voronoi diagram
    vor = Voronoi(coords)
    
    # Convert Voronoi regions to polygons
    from shapely.geometry import Polygon
    voronoi_polygons = []
    
    for point_idx, region_idx in enumerate(vor.point_region):
        region = vor.regions[region_idx]
        
        if len(region) == 0 or -1 in region:
            # Unbounded region - create a buffered point
            point = coords[point_idx]
            poly = Point(point).buffer(0.5)
        else:
            vertices = vor.vertices[region]
            poly = Polygon(vertices)
        
        voronoi_polygons.append({
            'geometry': poly,
            'cat': gdf.iloc[point_idx]['cat'],
            f'{scenario_name}_index': gdf.iloc[point_idx][f'{scenario_name}_index'],
            f'{scenario_name}_bin': gdf.iloc[point_idx][f'{scenario_name}_bin']
        })
    
    voronoi_gdf = gpd.GeoDataFrame(voronoi_polygons, crs=gdf.crs)
    
    # Clip to country boundary
    print(f"Clipping to country boundaries...")
    country = gpd.read_file(country_boundary)
    voronoi_gdf = gpd.clip(voronoi_gdf, country)
    
    print(f"Created {len(voronoi_gdf)} Voronoi polygons")
    
    return voronoi_gdf


def create_map_visualization(village_gdf, voronoi_gdf, power_lines_gdf, scenario_name, scenarios, country_boundary):
    """Create and export map visualization as PNG."""
    print(f"Creating map visualization for {scenario_name}...")
    
    # Load and get country boundary
    country = gpd.read_file(country_boundary)
    country = country.to_crs(OUTPUT_CRS)
    
    # Clip power lines to country
    power_lines_clipped = gpd.clip(power_lines_gdf, country)
    
    fig, ax = plt.subplots(figsize=(20, 16), dpi=200)
    
    # Map colors
    color_map_soft = {
        'red': COLOR_SCHEME['red_soft'],
        'yellow': COLOR_SCHEME['yellow_soft'],
        'green': COLOR_SCHEME['green_soft']
    }
    
    color_map_bright = {
        'red': COLOR_SCHEME['red_bright'],
        'yellow': COLOR_SCHEME['yellow_bright'],
        'green': COLOR_SCHEME['green_bright']
    }
    
    # Plot Voronoi polygons (soft colors, no edges)
    for idx, row in voronoi_gdf.iterrows():
        color = color_map_soft[row[f'{scenario_name}_bin']]
        gpd.GeoSeries(row.geometry).plot(ax=ax, color=color, edgecolor='none', alpha=0.8)
    
    # Plot country boundary for reference
    country.plot(ax=ax, color='none', edgecolor='black', linewidth=1.5, alpha=0.5)
    
    # Plot villages (bright colors, with edges) - reduced size to let grid lines shine through
    for bin_type in ['red', 'yellow', 'green']:
        mask = village_gdf[f'{scenario_name}_bin'] == bin_type
        if mask.any():
            subset = village_gdf[mask]
            subset.plot(
                ax=ax,
                color=color_map_bright[bin_type],
                markersize=40,
                edgecolor='black',
                linewidth=0.5,
                label=f'{bin_type.capitalize()} ({mask.sum()} villages)',
                alpha=0.6,
                zorder=10
            )
    
    # Plot power lines LAST (clipped to country) - prominently in foreground
    if len(power_lines_clipped) > 0:
        power_lines_clipped.plot(ax=ax, color='black', linewidth=2.8, alpha=0.87, label='Power Lines', zorder=15)
    
    # Set axis limits to country bounds
    minx, miny, maxx, maxy = country.total_bounds
    ax.set_xlim(minx - 0.5, maxx + 0.5)
    ax.set_ylim(miny - 0.5, maxy + 0.5)
    
    ax.set_title(f'Grid Connection Indicator - {scenario_name.upper().replace("_", " ")}\n'
                 f'{scenarios[scenario_name]["description"]}',
                 fontsize=18, fontweight='bold', pad=20)
    ax.set_xlabel('Longitude', fontsize=13)
    ax.set_ylabel('Latitude', fontsize=13)
    ax.legend(loc='upper left', fontsize=12, framealpha=0.98, edgecolor='black')
    ax.grid(True, alpha=0.15, linestyle='--', linewidth=0.5)
    
    # Export as PNG with high quality
    output_image = OUTPUT_DIR / f'grid_connection_{scenario_name}.png'
    plt.tight_layout()
    plt.savefig(output_image, format='png', dpi=200, bbox_inches='tight')
    plt.close()
    
    return output_image


def create_map_visualization_no_points(voronoi_gdf, power_lines_gdf, scenario_name, scenarios, country_boundary):
    """Create and export map visualization as PNG without village points."""
    print(f"Creating map visualization (no points) for {scenario_name}...")
    
    # Load and get country boundary
    country = gpd.read_file(country_boundary)
    country = country.to_crs(OUTPUT_CRS)
    
    # Clip power lines to country
    power_lines_clipped = gpd.clip(power_lines_gdf, country)
    
    fig, ax = plt.subplots(figsize=(20, 16), dpi=200)
    
    # Map colors
    color_map_soft = {
        'red': COLOR_SCHEME['red_soft'],
        'yellow': COLOR_SCHEME['yellow_soft'],
        'green': COLOR_SCHEME['green_soft']
    }
    
    # Plot Voronoi polygons (soft colors, no edges)
    for idx, row in voronoi_gdf.iterrows():
        color = color_map_soft[row[f'{scenario_name}_bin']]
        gpd.GeoSeries(row.geometry).plot(ax=ax, color=color, edgecolor='none', alpha=0.8)
    
    # Plot country boundary for reference
    country.plot(ax=ax, color='none', edgecolor='black', linewidth=1.5, alpha=0.5)
    
    # Plot power lines PROMINENTLY in foreground (no village points to obscure them)
    if len(power_lines_clipped) > 0:
        power_lines_clipped.plot(ax=ax, color='black', linewidth=2.8, alpha=0.87, label='Power Lines', zorder=15)
    
    # Set axis limits to country bounds
    minx, miny, maxx, maxy = country.total_bounds
    ax.set_xlim(minx - 0.5, maxx + 0.5)
    ax.set_ylim(miny - 0.5, maxy + 0.5)
    
    ax.set_title(f'Grid Connection Indicator - {scenario_name.upper().replace("_", " ")} (Power Lines Only)\n'
                 f'{scenarios[scenario_name]["description"]}',
                 fontsize=18, fontweight='bold', pad=20)
    ax.set_xlabel('Longitude', fontsize=13)
    ax.set_ylabel('Latitude', fontsize=13)
    ax.legend(loc='upper left', fontsize=12, framealpha=0.98, edgecolor='black')
    ax.grid(True, alpha=0.15, linestyle='--', linewidth=0.5)
    
    # Export as PNG with high quality
    output_image = OUTPUT_DIR / f'grid_connection_{scenario_name}_no_points.png'
    plt.tight_layout()
    plt.savefig(output_image, format='png', dpi=200, bbox_inches='tight')
    plt.close()
    
    return output_image


def export_scenario(df, scenario_name, scenarios, country_boundary, power_lines_path):
    """Export scenario: PNG visualization and data for Excel compilation."""
    
    print(f"\n{'=' * 60}")
    print(f"EXPORTING SCENARIO: {scenario_name.upper()}")
    print(f"{'=' * 60}")
    
    # Create GeoDataFrame for villages (points)
    geometry = [Point(xy) for xy in zip(df[LON_COLUMN], df[LAT_COLUMN])]
    
    village_gdf = gpd.GeoDataFrame(
        {
            'cat': df['cat'],
            f'{scenario_name}_index': df[f'{scenario_name}_index'],
            f'{scenario_name}_bin': df[f'{scenario_name}_bin'],
            'pop_sum_norm': df['pop_sum_norm'],
            'distance_norm': df['distance_grid_km_norm'],
            'rwi_norm': df['rwi_norm'],
            'density_norm': df['density_norm'],
            'demand_norm': df['demand_year1_kwh_norm'],
        },
        geometry=geometry,
        crs=OUTPUT_CRS
    )
    
    # Create Voronoi layer
    voronoi_gdf = create_voronoi_geodataframe(village_gdf, scenario_name, country_boundary)
    
    # Load power lines and reproject if needed
    print(f"Loading power lines...")
    power_lines_gdf = gpd.read_file(power_lines_path)
    if power_lines_gdf.crs != OUTPUT_CRS:
        power_lines_gdf = power_lines_gdf.to_crs(OUTPUT_CRS)
    
    # Create map visualization
    map_image = create_map_visualization(village_gdf, voronoi_gdf, power_lines_gdf, scenario_name, scenarios, country_boundary)
    print(f"✓ Exported map image to: {map_image}")
    
    # Create map visualization without village points
    map_image_no_points = create_map_visualization_no_points(voronoi_gdf, power_lines_gdf, scenario_name, scenarios, country_boundary)
    print(f"✓ Exported map image (no points) to: {map_image_no_points}")
    
    # Prepare data for Excel (without geometry)
    scenario_data = df[
        ['cat', 'pop_sum', 'distance_grid_km', 'rwi', 'density', 'demand_year1_kwh',
         'pop_sum_norm', 'distance_grid_km_norm', 'rwi_norm', 'density_norm', 'demand_year1_kwh_norm',
         f'{scenario_name}_index', f'{scenario_name}_bin']
    ].copy()
    
    return scenario_data


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 60)
    print("GRID CONNECTION INDICATOR CALCULATOR")
    print("=" * 60)
    
    # Read data
    print(f"\nReading CSV: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV)
    print(f"Loaded {len(df)} clusters with {len(df.columns)} columns")
    
    if LON_COLUMN not in df.columns or LAT_COLUMN not in df.columns:
        raise ValueError(f"Coordinate columns '{LON_COLUMN}' and/or '{LAT_COLUMN}' not found in CSV")
    
    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Calculate indicators
    result_df = calculate_indicators(df, SCENARIOS)
    
    # Create single output dataframe with all scenario indices
    output_df = result_df[['cat', 'pop_sum', 'distance_grid_km', 'rwi', 'density', 'demand_year1_kwh']].copy()
    
    # Add scenario indices and bins as columns
    for scenario_name in SCENARIOS.keys():
        output_df[f'grid_index_{scenario_name}'] = result_df[f'{scenario_name}_index']
        output_df[f'grid_bin_{scenario_name}'] = result_df[f'{scenario_name}_bin']
    
    # Export to single CSV file
    csv_path = OUTPUT_DIR / f'grid_connection_{COUNTRY}.csv'
    output_df.to_csv(csv_path, index=False)
    
    print(f"\n✓ Exported all scenarios to: {csv_path}")
    
    # Also generate PNG maps for visualizations
    for scenario_name in SCENARIOS.keys():
        export_scenario(result_df, scenario_name, SCENARIOS, COUNTRY_BOUNDARY, POWER_LINES)
    
    print("\n" + "=" * 60)
    print("COMPLETED SUCCESSFULLY")
    print("=" * 60)
    print(f"\nOutput directory: {OUTPUT_DIR}")
    print(f"\nFiles created:")
    print(f"  - grid_connection_{COUNTRY}.csv")
    for scenario_name in SCENARIOS.keys():
        print(f"  - grid_connection_{scenario_name}.png")


if __name__ == '__main__':
    main()
