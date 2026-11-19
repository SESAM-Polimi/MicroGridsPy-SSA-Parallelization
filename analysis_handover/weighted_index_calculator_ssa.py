#!/usr/bin/env python3


from pathlib import Path
from shapely.geometry import Point, box
from scipy.spatial import Voronoi
import argparse
import warnings
import shutil

import geopandas as gpd
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.path import Path as MplPath
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.patches import PathPatch

warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION
# ============================================================================

COUNTRIES_ROOT = Path('/Volumes/TOSHIBA EXT/OUTPUTS COUNTRY')
GADM_ROOT = Path('/Users/matteo/Desktop/Tesi/GADM')
POWER_LINES = Path('/Users/matteo/Desktop/Tesi/qgis/electricitygrid_Africa_JRC/elect_grid_africa_epsg3426_withgau_JRC.shp')
GRID_OVERLAY_LINES = Path('/Users/matteo/Desktop/Tesi/qgis/electricitygrid_Africa_JRC/clip_elect_grid.shp')
OUTPUT_DIR = Path('/Users/matteo/Desktop/SSA_maps_output/WeightedIndex')
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
GRID_BACKGROUND_VECTOR_PATH = OUTPUT_DIR / 'ssa_grid_background.gpkg'
GRID_BACKGROUND_IMAGE_PATH = OUTPUT_DIR / 'ssa_grid_background.png'
FAST_OVERLAY_SIMPLIFIED_PATH = OUTPUT_DIR / 'clip_elect_grid_simplified.shp'

LON_COLUMN = 'lon'
LAT_COLUMN = 'lat'
OUTPUT_CRS = 'EPSG:4326'

NO_DATA_COUNTRIES = {'SDN', 'SSD', 'ERI', 'SOM'}
# Tighten the northern extent so the weighted-index maps stay focused on SSA.
SSA_BOUNDS = box(-25.0, -40.0, 55.0, 25.0)
FAST_SSA_BOUNDS = box(-25.0, -40.0, 55.0, 30.0)

SCENARIOS = {
    'conservative': {
        'description': 'Economical - strict on distance and demand',
        'weights': {
            'distance_grid_km': 0.40,
            'demand_year1_kwh': 0.30,
            'pop_sum': 0.10,
            'density': 0.10,
            'rwi': 0.10,
        },
    },
    'demand_focused': {
        'description': 'Energy demand driven',
        'weights': {
            'distance_grid_km': 0.20,
            'demand_year1_kwh': 0.45,
            'pop_sum': 0.125,
            'density': 0.125,
            'rwi': 0.10,
        },
    },
    'people_focused': {
        'description': 'Equity - population and wealth focused',
        'weights': {
            'distance_grid_km': 0.05,
            'demand_year1_kwh': 0.10,
            'pop_sum': 0.35,
            'density': 0.35,
            'rwi': 0.15,
        },
    },
}

SCENARIO_DISPLAY_TITLES = {
    'conservative': 'Investment focused',
    'demand_focused': 'Demand focused',
    'people_focused': 'People focused',
}

BIN_THRESHOLDS = [0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0]

DISTANCE_BONUS_THRESHOLD = 10
DISTANCE_PENALTY_THRESHOLD = 150
BONUS_AMOUNT = 8
PENALTY_AMOUNT = 8

COLOR_SCHEME = {
    'red_bright': '#FF0000',
    'red_soft': '#FF6666',
    'yellow_bright': '#FFFF00',
    'yellow_soft': '#FFE066',
    'green_bright': '#00FF00',
    'green_soft': '#7CFC00',
}

# Additional intermediate colors for quintiles
COLOR_SCHEME['orange_soft'] = '#FFAA66'
COLOR_SCHEME['lightgreen_soft'] = '#ADFF2F'

OVERLAY_CLIP_BUFFER_DEGREES = 0.12


# ============================================================================
# DATA LOADING
# ============================================================================

def country_from_csv_path(csv_path: Path) -> str:
    return csv_path.stem.replace('_sample', '')


def load_all_country_data():
    """Load all available SSA country sample CSVs into one dataframe."""
    if not COUNTRIES_ROOT.exists():
        raise FileNotFoundError(f'Country data root not found: {COUNTRIES_ROOT}')

    frames = []
    csv_files = sorted(COUNTRIES_ROOT.glob('*_sample.csv'))
    print(f'Found {len(csv_files)} country CSV files', flush=True)

    for index, csv_path in enumerate(csv_files, 1):
        country = country_from_csv_path(csv_path)
        print(f'  [{index}/{len(csv_files)}] Reading {country}...', flush=True)
        try:
            df = pd.read_csv(csv_path)
        except Exception as exc:
            print(f'[WARN] Skipping {country}: unable to read {csv_path.name} ({exc})')
            continue

        if LON_COLUMN not in df.columns or LAT_COLUMN not in df.columns:
            print(f'[WARN] Skipping {country}: missing {LON_COLUMN}/{LAT_COLUMN}')
            continue

        df = df.copy()
        df['country'] = country
        frames.append(df)

    if not frames:
        raise RuntimeError(f'No *_sample.csv files found in {COUNTRIES_ROOT}')

    return pd.concat(frames, ignore_index=True)


def load_country_boundary(country_code: str):
    boundary_path = GADM_ROOT / f'gadm41_{country_code}.gpkg'
    if not boundary_path.exists():
        return None

    try:
        boundary = gpd.read_file(boundary_path)
    except Exception:
        return None

    if boundary.crs is None:
        boundary = boundary.set_crs(OUTPUT_CRS)
    else:
        boundary = boundary.to_crs(OUTPUT_CRS)
    return boundary


def load_power_lines():
    power_lines = gpd.read_file(POWER_LINES)
    if power_lines.crs != OUTPUT_CRS:
        power_lines = power_lines.to_crs(OUTPUT_CRS)
    return power_lines


def load_grid_overlay_lines():
    grid_lines = gpd.read_file(GRID_OVERLAY_LINES)
    if grid_lines.crs != OUTPUT_CRS:
        grid_lines = grid_lines.to_crs(OUTPUT_CRS)
    return grid_lines


def get_simplified_grid_overlay(tolerance=0.02, max_features=12000):
    """Return a simplified version of the grid overlay for fast plotting.

    This caches a simplified shapefile under OUTPUT_DIR to avoid repeating heavy
    simplification on subsequent runs.
    """
    try:
        if FAST_OVERLAY_SIMPLIFIED_PATH.exists():
            g = gpd.read_file(FAST_OVERLAY_SIMPLIFIED_PATH)
            if g.crs is None:
                g = g.set_crs(OUTPUT_CRS)
            else:
                g = g.to_crs(OUTPUT_CRS)
            return g

        # Read original and simplify
        g = gpd.read_file(GRID_OVERLAY_LINES)
        if g.crs != OUTPUT_CRS:
            g = g.to_crs(OUTPUT_CRS)

        # Simplify geometries
        g['geometry'] = g['geometry'].simplify(tolerance, preserve_topology=False)

        # If too many features, downsample uniformly
        n = len(g)
        if n > max_features:
            import numpy as _np

            idx = _np.linspace(0, n - 1, max_features, dtype=int)
            g = g.iloc[idx].reset_index(drop=True)

        # Save simplified overlay for reuse
        try:
            g.to_file(FAST_OVERLAY_SIMPLIFIED_PATH)
        except Exception:
            pass

        return g
    except Exception:
        return None


def load_grid_background():
    """Load a pre-clipped SSA grid background if it has been prepared separately."""
    if GRID_BACKGROUND_VECTOR_PATH.exists():
        try:
            grid_background = gpd.read_file(GRID_BACKGROUND_VECTOR_PATH)
            if grid_background.crs is None:
                grid_background = grid_background.set_crs(OUTPUT_CRS)
            else:
                grid_background = grid_background.to_crs(OUTPUT_CRS)
            return grid_background
        except Exception:
            pass

    if GRID_BACKGROUND_IMAGE_PATH.exists():
        try:
            return mpimg.imread(GRID_BACKGROUND_IMAGE_PATH)
        except Exception:
            return None

    return None


def plot_grid_background(ax, grid_background, bounds, clip_geometry=None, alpha=0.95, linewidth=0.85, zorder=4):
    """Draw the SSA grid background as either raw vector lines or a fallback raster."""
    if grid_background is None:
        return

    if isinstance(grid_background, gpd.GeoDataFrame):
        vector = grid_background
        minx, miny, maxx, maxy = bounds
        try:
            vector = vector.cx[minx:maxx, miny:maxy]
        except Exception:
            pass
        if clip_geometry is not None:
            try:
                vector = gpd.clip(vector, clip_geometry)
            except Exception:
                pass
        if len(vector) > 0:
            vector.plot(ax=ax, color='#2f2f2f', linewidth=max(linewidth, 1.15), alpha=alpha, zorder=zorder)
        return

    if isinstance(grid_background, np.ndarray):
        minx, miny, maxx, maxy = bounds
        grid_image = ax.imshow(
            grid_background,
            extent=(minx, maxx, miny, maxy),
            origin='upper',
            alpha=alpha,
            interpolation='nearest',
            zorder=zorder,
        )
        if clip_geometry is not None:
            clip_target = clip_geometry
            try:
                clip_target = clip_geometry.buffer(OVERLAY_CLIP_BUFFER_DEGREES)
            except Exception:
                pass
            clip_patch = build_clip_patch(clip_target, ax)
            if clip_patch is not None:
                grid_image.set_clip_path(clip_patch)


def load_gadm_ssa_boundaries():
    """Load all GADM country boundaries present in the GADM folder."""
    boundaries = []
    for boundary_path in sorted(GADM_ROOT.glob('gadm41_*.gpkg')):
        try:
            boundary = gpd.read_file(boundary_path)
        except Exception:
            continue

        if boundary.crs is None:
            boundary = boundary.set_crs(OUTPUT_CRS)
        else:
            boundary = boundary.to_crs(OUTPUT_CRS)

        if len(boundary) > 0:
            boundaries.append(boundary)

    return boundaries


def build_clip_patch(geometries, ax):
    """Build a Matplotlib clip patch from one or more Shapely geometries."""
    vertices = []
    codes = []

    def add_ring(coords):
        coords = np.asarray(coords)
        if len(coords) < 3:
            return
        if not np.allclose(coords[0], coords[-1]):
            coords = np.vstack([coords, coords[0]])
        n = len(coords)
        vertices.extend(coords.tolist())
        codes.extend([MplPath.MOVETO] + [MplPath.LINETO] * (n - 2) + [MplPath.CLOSEPOLY])

    def add_geometry(geometry):
        if geometry is None or geometry.is_empty:
            return
        geom_type = geometry.geom_type
        if geom_type == 'Polygon':
            add_ring(geometry.exterior.coords)
            for interior in geometry.interiors:
                add_ring(interior.coords)
        elif geom_type == 'MultiPolygon':
            for part in geometry.geoms:
                add_geometry(part)

    if hasattr(geometries, 'geoms'):
        for geometry in geometries.geoms:
            add_geometry(geometry)
    else:
        add_geometry(geometries)

    if not vertices:
        return None

    path = MplPath(np.asarray(vertices), codes)
    return PathPatch(path, facecolor='none', edgecolor='none', transform=ax.transData)


def copy_output_to_desktop(path: Path):
    """Copy a generated output to the Desktop WeightedIndex folder when available."""
    desktop_dest = Path('/Users/matteo/Desktop/SSA_maps_output/WeightedIndex')
    desktop_dest.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy(str(path), str(desktop_dest / path.name))
    except Exception as exc:
        print(f'[WARN] Could not copy {path.name} to Desktop output: {exc}')


def export_fast_maps_to_gpkg(result_df, scenario_names, gpkg_path: Path):
    """Export fast-mode scenario point layers to a single GeoPackage."""
    try:
        if gpkg_path.exists():
            gpkg_path.unlink()
    except Exception:
        pass

    base_gdf = gpd.GeoDataFrame(
        result_df.copy(),
        geometry=gpd.points_from_xy(result_df['lon'], result_df['lat']),
        crs=OUTPUT_CRS,
    )

    for scenario_name in scenario_names:
        layer_gdf = base_gdf[[
            'country',
            'cat',
            'pop_sum',
            'distance_grid_km',
            'rwi',
            'density',
            'demand_year1_kwh',
            f'{scenario_name}_index',
            f'{scenario_name}_bin',
            'geometry',
        ]].copy()
        layer_name = scenario_name[:63]
        layer_gdf.to_file(gpkg_path, layer=layer_name, driver='GPKG')


# ============================================================================
# INDICATOR CALCULATION
# ============================================================================

def normalize_to_100(series, direction='higher_better'):
    """Normalize a series to 0-100 range."""
    min_val = series.min()
    max_val = series.max()

    if pd.isna(min_val) or pd.isna(max_val) or max_val == min_val:
        return pd.Series([50.0] * len(series), index=series.index)

    normalised = (series - min_val) / (max_val - min_val)
    if direction == 'lower_better':
        normalised = 1 - normalised

    return normalised * 100


def calculate_indicators(df, scenarios, bins=3, quintile_map='default'):
    """Calculate weighted indicators for all scenarios over the full SSA dataset."""
    result_df = df.copy()

    parameters_config = {
        'pop_sum': 'higher_better',
        'distance_grid_km': 'lower_better',
        'rwi': 'higher_better',
        'density': 'higher_better',
        'demand_year1_kwh': 'higher_better',
    }

    print('\nNormalizing parameters:')
    print('-' * 60)
    for param_name, direction in parameters_config.items():
        normalised_col = f'{param_name}_norm'
        result_df[normalised_col] = normalize_to_100(df[param_name], direction)
        print(f'{param_name:20s} | Range: [{result_df[normalised_col].min():.2f}, {result_df[normalised_col].max():.2f}]')

    print('\n' + '=' * 60)
    print('CALCULATING WEIGHTED INDICES')
    print('=' * 60)

    for scenario_name, scenario_config in scenarios.items():
        weights = scenario_config['weights']
        weighted_index = pd.Series([0.0] * len(df), index=df.index)

        print(f'\n{scenario_name.upper()}: {scenario_config["description"]}')
        print('-' * 60)

        for param_name, weight in weights.items():
            normalised_col = f'{param_name}_norm'
            weighted_index += result_df[normalised_col] * weight
            print(f'  {param_name:20s}: {weight:5.1%}')

        distance_adj = pd.Series(0.0, index=result_df.index)
        close_mask = result_df['distance_grid_km'] < DISTANCE_BONUS_THRESHOLD
        far_mask = result_df['distance_grid_km'] > DISTANCE_PENALTY_THRESHOLD
        distance_adj[close_mask] = BONUS_AMOUNT
        distance_adj[far_mask] = -PENALTY_AMOUNT
        weighted_index = weighted_index + distance_adj

        result_df[f'{scenario_name}_index'] = np.clip(weighted_index / 100.0, 0, 1)

        # Bin by quantiles: support terciles (3) and quintiles (5).
        if bins == 3:
            labels = ['red', 'yellow', 'green']
            quantiles = [1/3, 2/3]
            q_values = result_df[f'{scenario_name}_index'].quantile(quantiles).values
            bins_edges = [-np.inf] + list(q_values) + [np.inf]
            result_df[f'{scenario_name}_bin'] = pd.cut(
                result_df[f'{scenario_name}_index'],
                bins=bins_edges,
                labels=labels,
                include_lowest=True,
            )
            print(f'  Index range: [{result_df[f"{scenario_name}_index"].min():.4f}, {result_df[f"{scenario_name}_index"].max():.4f}]')
            print(f'  Quantile thresholds: {q_values}')
            print('  Distribution (quantiles):')
            for lab in labels:
                print(f"    {lab:8s}: {(result_df[f'{scenario_name}_bin'] == lab).sum():6d} clusters")
        elif bins == 5:
            # Create integer quintile ids (0..4) using qcut, then map to desired labels.
            try:
                quint_ids = pd.qcut(result_df[f'{scenario_name}_index'], q=5, labels=False, duplicates='drop')
            except Exception:
                quint_ids = pd.cut(result_df[f'{scenario_name}_index'], bins=5, labels=False)

            q_values = result_df[f'{scenario_name}_index'].quantile([i / 5.0 for i in range(1, 5)]).values

            if quintile_map == 'default':
                id_to_label = {0: 'red', 1: 'orange', 2: 'yellow', 3: 'lightgreen', 4: 'green'}
            elif quintile_map == '2red_34yellow':
                id_to_label = {0: 'red', 1: 'red', 2: 'yellow', 3: 'yellow', 4: 'green'}
            else:
                id_to_label = {0: 'red', 1: 'orange', 2: 'yellow', 3: 'lightgreen', 4: 'green'}

            result_df[f'{scenario_name}_bin'] = quint_ids.map(lambda i: id_to_label.get(i, 'yellow'))
            result_df[f'{scenario_name}_bin'] = result_df[f'{scenario_name}_bin'].astype('category')

            print(f'  Index range: [{result_df[f"{scenario_name}_index"].min():.4f}, {result_df[f"{scenario_name}_index"].max():.4f}]')
            print(f'  Quintile thresholds: {q_values}')
            print('  Distribution (quintiles -> labels):')
            for lab in sorted(result_df[f'{scenario_name}_bin'].unique()):
                print(f"    {lab:8s}: {(result_df[f'{scenario_name}_bin'] == lab).sum():6d} clusters")
        else:
            # fallback: same as terciles
            labels = ['red', 'yellow', 'green']
            quantiles = [1/3, 2/3]
            q_values = result_df[f'{scenario_name}_index'].quantile(quantiles).values
            bins_edges = [-np.inf] + list(q_values) + [np.inf]
            result_df[f'{scenario_name}_bin'] = pd.cut(
                result_df[f'{scenario_name}_index'],
                bins=bins_edges,
                labels=labels,
                include_lowest=True,
            )
            print(f'  Index range: [{result_df[f"{scenario_name}_index"].min():.4f}, {result_df[f"{scenario_name}_index"].max():.4f}]')
            print(f'  Quantile thresholds: {q_values}')
            print('  Distribution (quantiles):')
            for lab in labels:
                print(f"    {lab:8s}: {(result_df[f'{scenario_name}_bin'] == lab).sum():6d} clusters")

    return result_df


# ============================================================================
# VORONOI BUILDING
# ============================================================================

def create_voronoi_geodataframe(gdf, scenario_name, country_boundary_path):
    """Create Voronoi polygons for one country and clip them to that country boundary."""
    coords = np.array([[p.x, p.y] for p in gdf.geometry])
    if len(coords) == 0:
        return None

    country = gpd.read_file(country_boundary_path)
    if country.crs is None:
        country = country.set_crs(OUTPUT_CRS)
    else:
        country = country.to_crs(OUTPUT_CRS)

    country = gpd.clip(country, gpd.GeoDataFrame(geometry=[SSA_BOUNDS], crs=OUTPUT_CRS))
    if len(country) == 0:
        return None

    if len(coords) < 3:
        records = []
        for idx, row in gdf.iterrows():
            geom = row.geometry.buffer(0.2)
            records.append({
                'geometry': geom,
                'country': row.get('country', None),
                'cat': row['cat'],
                f'{scenario_name}_index': row[f'{scenario_name}_index'],
                f'{scenario_name}_bin': row[f'{scenario_name}_bin'],
            })
        return gpd.GeoDataFrame(records, crs=OUTPUT_CRS)

    vor = Voronoi(coords)
    voronoi_polygons = []

    for point_idx, region_idx in enumerate(vor.point_region):
        region = vor.regions[region_idx]
        if len(region) == 0 or -1 in region:
            poly = Point(coords[point_idx]).buffer(0.35)
        else:
            from shapely.geometry import Polygon
            poly = Polygon(vor.vertices[region])

        row = gdf.iloc[point_idx]
        voronoi_polygons.append({
            'geometry': poly,
            'country': row.get('country', None),
            'cat': row['cat'],
            f'{scenario_name}_index': row[f'{scenario_name}_index'],
            f'{scenario_name}_bin': row[f'{scenario_name}_bin'],
        })

    voronoi_gdf = gpd.GeoDataFrame(voronoi_polygons, crs=gdf.crs)
    voronoi_gdf = gpd.clip(voronoi_gdf, country)
    return voronoi_gdf


def build_ssa_voronoi_layers(result_df, scenario_name, country_codes):
    """Build a combined Voronoi GeoDataFrame across SSA, country by country."""
    parts = []
    total_countries = len(country_codes)
    print(f'Building Voronoi layers for {total_countries} countries ({scenario_name})', flush=True)

    for index, country_code in enumerate(country_codes, 1):
        print(f'  [{index}/{total_countries}] Voronoi for {country_code}...', flush=True)
        if country_code in NO_DATA_COUNTRIES:
            print(f'    -> skipped (no data country)', flush=True)
            continue
        country_df = result_df[result_df['country'] == country_code].copy()
        if len(country_df) == 0:
            print(f'    -> skipped (no clusters found)', flush=True)
            continue

        boundary_path = GADM_ROOT / f'gadm41_{country_code}.gpkg'
        if not boundary_path.exists():
            print(f'[WARN] Missing boundary for {country_code}; skipping Voronoi layer')
            continue

        print(f'    -> {len(country_df):,} clusters, building polygons...', flush=True)

        country_points = gpd.GeoDataFrame(
            {
                'cat': country_df['cat'],
                'country': country_df['country'],
                f'{scenario_name}_index': country_df[f'{scenario_name}_index'],
                f'{scenario_name}_bin': country_df[f'{scenario_name}_bin'],
            },
            geometry=gpd.points_from_xy(country_df[LON_COLUMN], country_df[LAT_COLUMN]),
            crs=OUTPUT_CRS,
        )

        country_voronoi = create_voronoi_geodataframe(country_points, scenario_name, boundary_path)
        if country_voronoi is not None and len(country_voronoi) > 0:
            print(f'    -> created {len(country_voronoi):,} polygons', flush=True)
            parts.append(country_voronoi)
        else:
            print(f'    -> no polygons returned', flush=True)

    if not parts:
        return None

    combined = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=OUTPUT_CRS)
    return combined


# ============================================================================
# MAP EXPORT
# ============================================================================

def create_ssa_map_visualization(voronoi_gdf, country_boundaries, no_data_boundaries, power_lines_gdf, scenario_name, scenarios):
    """Create and export a single SSA-wide map with polygon bins and grid overlay."""
    print(f'Creating SSA map visualization for {scenario_name}...', flush=True)

    all_boundary_frames = []
    for boundary in country_boundaries.values():
        all_boundary_frames.append(boundary)
    for boundary in no_data_boundaries.values():
        all_boundary_frames.append(boundary)

    if not all_boundary_frames:
        raise RuntimeError('No country boundaries available to build the SSA map')

    boundary_union = gpd.GeoDataFrame(pd.concat(all_boundary_frames, ignore_index=True), crs=OUTPUT_CRS)
    boundary_clip = boundary_union.geometry.unary_union
    if power_lines_gdf.crs != OUTPUT_CRS:
        power_lines_gdf = power_lines_gdf.to_crs(OUTPUT_CRS)
    power_lines_clipped = gpd.clip(power_lines_gdf, boundary_union)

    fig, ax = plt.subplots(figsize=(20, 16), dpi=160)

    color_map_soft = {
        'red': COLOR_SCHEME['red_soft'],
        'orange': COLOR_SCHEME['orange_soft'],
        'yellow': COLOR_SCHEME['yellow_soft'],
        'lightgreen': COLOR_SCHEME['lightgreen_soft'],
        'green': COLOR_SCHEME['green_soft'],
    }

    # Grey no-data countries first.
    print('  Rendering no-data countries...', flush=True)
    for country_code, boundary in no_data_boundaries.items():
        boundary.plot(ax=ax, color='#e6e6e6', edgecolor='#8c8c8c', linewidth=0.8, alpha=0.95, zorder=1)
        representative = boundary.geometry.unary_union.representative_point()
        ax.text(representative.x, representative.y, f'{country_code}\nNo data', ha='center', va='center', fontsize=8, color='#555555', zorder=4)

    # Data polygons by bin. Iterate over the categorical bin labels (supports 3 or 5 bins).
    print('  Rendering data polygons by bin...', flush=True)
    if pd.api.types.is_categorical_dtype(voronoi_gdf[f'{scenario_name}_bin']):
        bin_categories = list(voronoi_gdf[f'{scenario_name}_bin'].cat.categories)
    else:
        bin_categories = ['red', 'yellow', 'green']

    for bin_name in bin_categories:
        subset = voronoi_gdf[voronoi_gdf[f'{scenario_name}_bin'] == bin_name]
        if len(subset) > 0:
            print(f'    {bin_name}: {len(subset):,} polygons', flush=True)
            color_key = bin_name if bin_name in ['red','yellow','green'] else ('orange' if bin_name=='orange' else 'lightgreen')
            soft_key = f"{color_key}_soft"
            subset.plot(ax=ax, color=COLOR_SCHEME.get(soft_key, COLOR_SCHEME['yellow_soft']), edgecolor='none', alpha=0.86, zorder=2)

        # Grid overlay rendered beneath data from the pre-rendered background image.
        print('  Rendering grid overlay (background)...', flush=True)
        grid_background = load_grid_background()
        if grid_background is not None and len(grid_background) > 0:
            minx, miny, maxx, maxy = SSA_BOUNDS.bounds
            grid_image = ax.imshow(grid_background, extent=(minx, maxx, miny, maxy), origin='upper', alpha=0.35, zorder=1)
            clip_patch = build_clip_patch(boundary_clip, ax)
            if clip_patch is not None:
                grid_image.set_clip_path(clip_patch)
        else:
            minx, miny, maxx, maxy = SSA_BOUNDS.bounds

        # Country outlines.
        print('  Rendering country outlines...', flush=True)
        for boundary in country_boundaries.values():
            boundary.boundary.plot(ax=ax, color='black', linewidth=0.2, alpha=0.2, zorder=3)

        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)

    ax.set_title(
        f'Sub-Saharan Africa Weighted Index - {scenario_name.upper().replace("_", " ")}\n{scenarios[scenario_name]["description"]}',
        fontsize=18,
        fontweight='bold',
        pad=20,
    )
    ax.set_xlabel('Longitude', fontsize=13)
    ax.set_ylabel('Latitude', fontsize=13)
    ax.grid(True, alpha=0.15, linestyle='--', linewidth=0.5)

    legend_handles = [
        Patch(facecolor=COLOR_SCHEME['red_soft'], edgecolor='none', label='Low'),
        Patch(facecolor=COLOR_SCHEME['yellow_soft'], edgecolor='none', label='Medium'),
        Patch(facecolor=COLOR_SCHEME['green_soft'], edgecolor='none', label='High'),
        Patch(facecolor='#e6e6e6', edgecolor='#8c8c8c', label='No data'),
        Line2D([0], [0], color='black', lw=2.2, label='Grid overlay'),
    ]
    ax.legend(handles=legend_handles, loc='upper left', fontsize=11, framealpha=0.98, edgecolor='black')

    output_image = OUTPUT_DIR / f'ssa_grid_connection_{scenario_name}.png'
    plt.tight_layout()
    print('  Saving figure...', flush=True)
    plt.savefig(output_image, format='png', dpi=160)
    plt.close()

    return output_image


def create_zoomed_submaps_fast(result_df, scenario_name, country_boundaries, no_data_boundaries, power_lines_gdf, n=5, point_size=3.6, zoom_deg=0.7):
    """Create small zoomed-in PNGs for the top-n clusters by index for a scenario.

    - Selects the top `n` rows by `{scenario_name}_index`.
    - Creates an extent of lon/lat ± `zoom_deg` degrees around each point and renders
      a small map showing surrounding points and boundaries.
    - Saves outputs to OUTPUT_DIR / 'submaps'.
    """
    key = f'{scenario_name}_index'
    if key not in result_df.columns:
        print(f'[WARN] index column {key} not present; skipping submaps')
        return []

    valid = result_df.dropna(subset=['lon', 'lat', key]).copy()
    if valid.empty:
        return []

    topn = valid.sort_values(key, ascending=False).head(n)
    out_dir = OUTPUT_DIR / 'submaps'
    out_dir.mkdir(parents=True, exist_ok=True)
    created = []

    for i, row in enumerate(topn.itertuples(), 1):
        lon = getattr(row, 'lon')
        lat = getattr(row, 'lat')
        country = getattr(row, 'country', 'unknown')
        if pd.isna(lon) or pd.isna(lat):
            continue

        minx = lon - zoom_deg
        maxx = lon + zoom_deg
        miny = lat - zoom_deg
        maxy = lat + zoom_deg

        fig, ax = plt.subplots(figsize=(6, 5), dpi=180)

        # Plot any no-data boundaries that intersect extent.
        for bc, boundary in no_data_boundaries.items():
            try:
                clipped = boundary.cx[minx:maxx, miny:maxy]
                if len(clipped) > 0:
                    clipped.plot(ax=ax, color='#e6e6e6', edgecolor='#8c8c8c', linewidth=0.6, alpha=0.95, zorder=1)
            except Exception:
                pass

        # Plot country boundaries that intersect extent.
        for boundary in country_boundaries.values():
            try:
                clipped = boundary.cx[minx:maxx, miny:maxy]
                if len(clipped) > 0:
                    clipped.plot(ax=ax, facecolor='none', edgecolor='black', linewidth=0.25, alpha=0.25, zorder=3)
            except Exception:
                pass

        # Points within extent
        window_mask = (
            (result_df['lon'] >= minx) & (result_df['lon'] <= maxx) &
            (result_df['lat'] >= miny) & (result_df['lat'] <= maxy)
        )
        window = result_df[window_mask]

        # Reuse color mapping
        color_map_soft = {
            'red': COLOR_SCHEME['red_soft'],
            'orange': COLOR_SCHEME['orange_soft'],
            'yellow': COLOR_SCHEME['yellow_soft'],
            'lightgreen': COLOR_SCHEME['lightgreen_soft'],
            'green': COLOR_SCHEME['green_soft'],
        }

        if pd.api.types.is_categorical_dtype(result_df[f'{scenario_name}_bin']):
            point_bins = list(result_df[f'{scenario_name}_bin'].cat.categories)
        else:
            point_bins = ['red', 'yellow', 'green']

        for bin_name in reversed(point_bins):
            mask = window[f'{scenario_name}_bin'] == bin_name
            subset = window[mask]
            if len(subset) == 0:
                continue
            color = color_map_soft.get(bin_name, COLOR_SCHEME['yellow_soft'])
            ax.scatter(subset['lon'].values, subset['lat'].values, s=point_size, c=color, alpha=0.35, linewidths=0, zorder=2)

        # Highlight the focal point
        ax.scatter([lon], [lat], s=point_size * 4.0, c='none', edgecolor='#222222', linewidth=0.9, zorder=5)

        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)
        ax.set_title(f'Zoom {i}/{n} — {scenario_name} — {country} ({lon:.3f}, {lat:.3f})', fontsize=10)
        ax.set_xlabel('Lon')
        ax.set_ylabel('Lat')
        ax.grid(True, alpha=0.12)

        out_path = out_dir / f'ssa_grid_connection_{scenario_name}_zoom_{i}_{country}_{int(lon*1000)}_{int(lat*1000)}.png'
        plt.tight_layout()
        plt.savefig(out_path, dpi=180)
        plt.close()
        created.append(out_path)

    return created


def create_ssa_map_visualization_fast(result_df, country_boundaries, no_data_boundaries, power_lines_gdf, scenario_name, scenarios):
    """Fast SSA map visualization that plots points instead of Voronoi polygons.

    This mode avoids heavy polygon building and clipping and is suitable for quick previews
    and full-SSA outputs when Voronoi detail is not strictly necessary.
    """
    print(f'Creating FAST SSA map visualization for {scenario_name}...', flush=True)

    all_boundary_frames = []
    for boundary in country_boundaries.values():
        all_boundary_frames.append(boundary)
    for boundary in no_data_boundaries.values():
        all_boundary_frames.append(boundary)
    overlay_clip = None
    if all_boundary_frames:
        try:
            overlay_clip = gpd.GeoDataFrame(pd.concat(all_boundary_frames, ignore_index=True), crs=OUTPUT_CRS).geometry.unary_union
        except Exception:
            overlay_clip = None

    # Ensure we have lon/lat
    if 'lon' not in result_df.columns or 'lat' not in result_df.columns:
        raise ValueError('Result dataframe must contain lon/lat columns for fast rendering')

    # Use a slightly lower DPI/size to speed rendering while keeping good quality
    FAST_FIGSIZE = (18, 14)
    FAST_DPI = 100
    fig, ax = plt.subplots(figsize=FAST_FIGSIZE, dpi=FAST_DPI)

    # Grey no-data countries first.
    for country_code, boundary in no_data_boundaries.items():
        boundary.plot(ax=ax, color='#e6e6e6', edgecolor='#8c8c8c', linewidth=0.8, alpha=0.95, zorder=1)
        representative = boundary.geometry.unary_union.representative_point()
        ax.text(representative.x, representative.y, f'{country_code}\nNo data', ha='center', va='center', fontsize=8, color='#555555', zorder=4)

    color_map_soft = {
        'red': COLOR_SCHEME['red_soft'],
        'orange': COLOR_SCHEME['orange_soft'],
        'yellow': COLOR_SCHEME['yellow_soft'],
        'lightgreen': COLOR_SCHEME['lightgreen_soft'],
        'green': COLOR_SCHEME['green_soft'],
    }

    # Plot points by bin. Draw higher bins first so lower bins remain visible on top.
    if pd.api.types.is_categorical_dtype(result_df[f'{scenario_name}_bin']):
        point_bins = list(result_df[f'{scenario_name}_bin'].cat.categories)
    else:
        point_bins = ['red', 'yellow', 'green']

    for bin_name in reversed(point_bins):
        mask = result_df[f'{scenario_name}_bin'] == bin_name
        subset = result_df[mask]
        if len(subset) == 0:
            continue
        print(f'  plotting {len(subset):,} points for bin {bin_name}', flush=True)
        color = color_map_soft.get(bin_name, COLOR_SCHEME['yellow_soft'])
        ax.scatter(subset['lon'].values, subset['lat'].values, s=1.6, c=color, alpha=0.24, linewidths=0, zorder=2)

    # Country outlines.
    for boundary in country_boundaries.values():
        boundary.boundary.plot(ax=ax, color='black', linewidth=0.35, alpha=0.35, zorder=3)

    minx, miny, maxx, maxy = FAST_SSA_BOUNDS.bounds

    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)

    ax.set_title(
        SCENARIO_DISPLAY_TITLES.get(scenario_name, scenario_name.replace('_', ' ').title()),
        fontsize=18,
        fontweight='bold',
        pad=16,
    )
    ax.set_xlabel('Longitude', fontsize=13)
    ax.set_ylabel('Latitude', fontsize=13)
    ax.grid(True, alpha=0.15, linestyle='--', linewidth=0.5)

    output_image = OUTPUT_DIR / f'ssa_grid_connection_{scenario_name}_fast.png'
    plt.tight_layout()
    print('  Saving FAST figure...', flush=True)
    plt.savefig(output_image, format='png', dpi=FAST_DPI)
    plt.close()

    return output_image


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description='SSA-wide weighted index calculator')
    parser.add_argument('--mode', choices=['voronoi','fast_scatter'], default='voronoi', help='Rendering mode: voronoi (default) or fast_scatter (much faster)')
    parser.add_argument('--bins', choices=['tercile'], default='tercile', help='Binning: tercile (3)')
    parser.add_argument('--submaps', type=int, default=0, help='Number of top clusters to export as zoomed submaps (fast mode only; 0 disables)')
    parser.add_argument('--only-scenario', choices=list(SCENARIOS.keys()), default=None, help='If set, only render this scenario (faster)')
    parser.add_argument('--sample', type=float, default=1.0, help='Fraction [0-1] of clusters to plot in fast_scatter mode (1.0 = all)')
    args = parser.parse_args()

    print('=' * 60)
    print('SSA W E I G H T E D   I N D E X   C A L C U L A T O R')
    print('=' * 60)
    print('Stage 1/5: reading country CSVs', flush=True)

    print('\nReading SSA country CSVs...', flush=True)
    df = load_all_country_data()
    print(f"Loaded {len(df):,} clusters across {df['country'].nunique()} countries")

    if LON_COLUMN not in df.columns or LAT_COLUMN not in df.columns:
        raise ValueError(f"Coordinate columns '{LON_COLUMN}' and/or '{LAT_COLUMN}' not found in the combined CSV data")

    print('\nStage 2/5: calculating indicators', flush=True)
    result_df = calculate_indicators(df, SCENARIOS, bins=3)

    output_df = result_df[['country', 'cat', 'pop_sum', 'distance_grid_km', 'rwi', 'density', 'demand_year1_kwh']].copy()
    for scenario_name in SCENARIOS.keys():
        output_df[f'grid_index_{scenario_name}'] = result_df[f'{scenario_name}_index']
        output_df[f'grid_bin_{scenario_name}'] = result_df[f'{scenario_name}_bin']

    csv_path = OUTPUT_DIR / 'grid_connection_ssa.csv'
    output_df.to_csv(csv_path, index=False)
    print(f'\n✓ Exported all scenarios to: {csv_path}', flush=True)

    gpkg_path = OUTPUT_DIR / 'grid_connection_ssa.gpkg'
    print(f'Exporting scenario layers to: {gpkg_path}', flush=True)
    export_fast_maps_to_gpkg(result_df, SCENARIOS.keys(), gpkg_path)
    print(f'✓ Exported all scenarios to: {gpkg_path}', flush=True)

    country_codes = sorted(set(result_df['country'].dropna().unique()) | NO_DATA_COUNTRIES)
    data_country_codes = [c for c in country_codes if c not in NO_DATA_COUNTRIES]
    country_boundaries = {}
    for country_code in data_country_codes:
        boundary = load_country_boundary(country_code)
        if boundary is not None and len(boundary) > 0:
            country_boundaries[country_code] = boundary

    no_data_boundaries = {}
    for country_code in sorted(NO_DATA_COUNTRIES):
        boundary = load_country_boundary(country_code)
        if boundary is not None and len(boundary) > 0:
            no_data_boundaries[country_code] = boundary
        else:
            print(f'[WARN] No boundary found for no-data country {country_code}')

    power_lines_gdf = load_power_lines() if args.mode == 'voronoi' else None

    # Prepare scenario list: either all scenarios or only the requested one
    scenario_list = [args.only_scenario] if args.only_scenario is not None else list(SCENARIOS.keys())

    # Optionally downsample clusters for plotting (affects only fast_scatter plotting, not CSV export)
    plot_df = result_df
    if args.sample is not None and 0.0 < args.sample < 1.0:
        try:
            plot_df = result_df.sample(frac=args.sample, random_state=1)
            print(f'[INFO] Downsampled plotting dataframe to {len(plot_df):,} clusters (fraction={args.sample})')
        except Exception:
            plot_df = result_df

    for scenario_name in scenario_list:
        if args.mode == 'voronoi':
            print(f'\nStage 3/5: building Voronoi polygons for {scenario_name}', flush=True)
            voronoi_gdf = build_ssa_voronoi_layers(result_df, scenario_name, data_country_codes)
            if voronoi_gdf is None or len(voronoi_gdf) == 0:
                print(f'[WARN] No Voronoi polygons created for {scenario_name}; skipping map export')
                continue

            print(f'Stage 4/5: rendering and saving {scenario_name} map', flush=True)
            map_image = create_ssa_map_visualization(
                voronoi_gdf,
                country_boundaries,
                no_data_boundaries,
                power_lines_gdf,
                scenario_name,
                SCENARIOS,
            )
            print(f'✓ Exported SSA map image to: {map_image}')
        else:
            # Fast scatter mode: avoid building Voronoi polygons and heavy clipping operations.
            print(f'\nStage 3/5: rendering FAST scatter map for {scenario_name}', flush=True)
            map_image = create_ssa_map_visualization_fast(
                plot_df,
                country_boundaries,
                no_data_boundaries,
                power_lines_gdf,
                scenario_name,
                SCENARIOS,
            )
            print(f'✓ Exported FAST SSA map image to: {map_image}')
            copy_output_to_desktop(map_image)

    print('\n' + '=' * 60)
    print('COMPLETED SUCCESSFULLY')
    print('=' * 60)
    print(f'\nOutput directory: {OUTPUT_DIR}')
    print('\nFiles created:')
    print('  - grid_connection_ssa.csv')
    print('  - grid_connection_ssa.gpkg')
    for scenario_name in SCENARIOS.keys():
        print(f'  - ssa_grid_connection_{scenario_name}.png')


if __name__ == '__main__':
    main()
