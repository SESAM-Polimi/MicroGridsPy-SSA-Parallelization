"""
Affordability Analysis Script
Calculates two affordability indicators for minigrid clusters:
1. Method 1: Household-level affordability by tier (LCOE × demand vs income)
2. Method 2: Community-level cost burden (total system cost per capita)

All costs and income values are converted to EUR for consistent comparison.
Income data (originally in 2011 PPP USD) is converted using EUR/USD = 1.17

Outputs a GPKG with all original columns plus affordability metrics.
"""
import pandas as pd
import geopandas as gpd
import numpy as np
from pathlib import Path
from shapely.geometry import Point
from scipy.stats import norm
from tqdm import tqdm
import argparse
import sys
import matplotlib.pyplot as plt
from typing import List
from matplotlib.gridspec import GridSpec
from matplotlib.patches import Patch
# Try to reuse palette from weighted index calculator when available
try:
    from weighted_index_calculator_ssa import COLOR_SCHEME as WI_COLOR_SCHEME
except Exception:
    WI_COLOR_SCHEME = {
        'red_soft': '#FF6666',
        'yellow_soft': '#FFE066',
        'green_soft': '#7CFC00',
        'lightgreen_soft': '#ADFF2F',
    }

# Add MGPY to path for archetype-based demand calculation
sys.path.insert(0, "/Users/matteo/Desktop/MicroGridsPy-Development_Linopy-2")
from config.path_manager import PathManager

# Set up PathManager for archetypes (must be done BEFORE importing demand_calculation)
PathManager.ARCHETYPES_FOLDER_PATH = Path("/Users/matteo/Desktop/MicroGridsPy-Development_Linopy-2/microgridspy/utils/Demand_archetypes")

# Now import demand_calculation after PathManager is configured
from microgridspy.utils.archetypes import demand_calculation

SSA_OUTPUT_ROOT = Path('/Volumes/TOSHIBA EXT/map/SSA')

# ============================================================================
# ARCHETYPE DEMAND CALCULATION WITH CACHING
# ============================================================================

# Global cache for demand_calculation results to avoid reloading Excel files
_demand_cache = {}
# Cache for archetype single-household base profiles (per zone+cooling)
_archetype_base_cache = {}

def cached_demand_calculation(lat, cooling_period, h_tier1, h_tier2, h_tier3, h_tier4, h_tier5,
                              num_schools, h_hosp1, h_hosp2, h_hosp3, h_hosp4, h_hosp5,
                              demand_growth, years, periods):
    """
    Wrapper around demand_calculation with caching to avoid repeated Excel file loads.
    Cache key is based on cooling_period, zone (derived from lat), and tier configuration.
    """
    # Determine zone from latitude (matches determine_zone logic in archetypes.py)
    if 10 <= lat <= 30:
        zone = 'F1'
    elif -10 <= lat < 10:
        zone = 'F2'
    elif -20 <= lat < -10:
        zone = 'F3'
    elif -30 <= lat < -20:
        zone = 'F4'
    elif lat < -30:
        zone = 'F5'
    else:
        zone = None  # Invalid latitude
    
    # Create cache key from zone, cooling, and tier configuration
    cache_key = (zone, cooling_period, h_tier1, h_tier2, h_tier3, h_tier4, h_tier5,
                 num_schools, h_hosp1, h_hosp2, h_hosp3, h_hosp4, h_hosp5, years, periods)
    
    if cache_key in _demand_cache:
        return _demand_cache[cache_key]
    
    # Not in cache, compute it
    result = demand_calculation(
        lat=lat,
        cooling_period=cooling_period,
        num_h_tier1=h_tier1,
        num_h_tier2=h_tier2,
        num_h_tier3=h_tier3,
        num_h_tier4=h_tier4,
        num_h_tier5=h_tier5,
        num_schools=num_schools,
        num_hospitals1=h_hosp1,
        num_hospitals2=h_hosp2,
        num_hospitals3=h_hosp3,
        num_hospitals4=h_hosp4,
        num_hospitals5=h_hosp5,
        demand_growth=demand_growth,
        years=years,
        periods=periods
    )
    
    # Store in cache
    _demand_cache[cache_key] = result
    return result


def get_zone_from_lat(lat):
    if lat is None or pd.isna(lat):
        return None
    if 10 <= lat <= 30:
        return 'F1'
    elif -10 <= lat < 10:
        return 'F2'
    elif -20 <= lat < -10:
        return 'F3'
    elif -30 <= lat < -20:
        return 'F4'
    elif lat < -30:
        return 'F5'
    else:
        return None


def get_archetype_base(lat, cooling_period, years=1, periods=24):
    """
    Precompute per-tier single-household annual kWh templates for the given zone+cooling.
    Returns dict {1: per_hh_kwh, ..., 5: per_hh_kwh}
    """
    zone = get_zone_from_lat(lat)
    key = (zone, cooling_period, years, periods)
    if key in _archetype_base_cache:
        return _archetype_base_cache[key]

    # For each tier, call demand_calculation with a single household in that tier
    base = {}
    for tier in range(1, 6):
        kwargs = {
            'lat': lat,
            'cooling_period': cooling_period,
            'h_tier1': 1 if tier == 1 else 0,
            'h_tier2': 1 if tier == 2 else 0,
            'h_tier3': 1 if tier == 3 else 0,
            'h_tier4': 1 if tier == 4 else 0,
            'h_tier5': 1 if tier == 5 else 0,
            'num_schools': 0,
            'h_hosp1': 0,
            'h_hosp2': 0,
            'h_hosp3': 0,
            'h_hosp4': 0,
            'h_hosp5': 0,
            'demand_growth': 0.02,
            'years': years,
            'periods': periods,
        }

        try:
            load_total, users = cached_demand_calculation(**kwargs)
        except Exception:
            base[tier] = np.nan
            continue

        # Sum Year_1 demand for the single household user
        per_hh_kwh = np.nan
        for user in users:
            if getattr(user, 'name', '').startswith(f'Household_Tier_{tier}'):
                try:
                    year1 = user.demand_data['Year_1'].sum()
                    per_hh_kwh = float(year1) / 1000.0  # convert Wh to kWh
                except Exception:
                    per_hh_kwh = np.nan
                break

        base[tier] = per_hh_kwh

    _archetype_base_cache[key] = base
    return base

# ============================================================================
# PARETO-LOGNORMAL DISTRIBUTION HELPERS (Hruschka et al. 2015)
# ============================================================================

def gini_to_sigma(gini):
    """Convert Gini to sigma for lognormal distribution."""
    return np.sqrt(2) * norm.ppf((gini + 1) / 2)


def gini_to_alpha(gini):
    """Convert Gini to alpha for Pareto distribution."""
    return (1 + gini) / (2 * gini)


def wealth_from_pareto(rank, wealth_percap, gini):
    """Wealth from Pareto inverse CDF."""
    alpha = gini_to_alpha(gini)
    threshold = (1 - (1 / alpha)) * wealth_percap
    return threshold / ((1 - rank) ** (1 / alpha))


def wealth_from_lognormal(rank, wealth_percap, gini):
    """Wealth from lognormal inverse CDF."""
    sigma = gini_to_sigma(gini)
    mu = np.log(wealth_percap) - (sigma ** 2) / 2
    return np.exp(mu + sigma * norm.ppf(rank))


def wealth_combined(rank, wealth_percap, gini, gamma=0.32):
    """Combined Pareto-Lognormal (gamma=0.32 optimal per paper)."""
    rank = np.clip(rank, 0.02, 0.98)
    w_pareto = wealth_from_pareto(rank, wealth_percap, gini)
    w_lognormal = wealth_from_lognormal(rank, wealth_percap, gini)
    return (w_lognormal ** (1 - gamma)) * (w_pareto ** gamma)

# ============================================================================
# CONFIGURATION
# ============================================================================
COUNTRY = "ZAF"

# Paths
PROJECTS_DIR = Path(f'/Volumes/TOSHIBA EXT/outputs_{COUNTRY}_advanced')
SAMPLE_CSV = Path(f'/Volumes/TOSHIBA EXT/OUTPUTS COUNTRY/{COUNTRY}_sample.csv')
INCOME_CSV = Path(f'/Volumes/TOSHIBA EXT/OUTPUTS COUNTRY/{COUNTRY}_clusters_income.csv')
HOUSEHOLD_SIZE_CSV = Path('/Users/matteo/Desktop/Tesi/Data sheet/household_number.csv')
# Output to Toshiba drive only (no desktop fallback - no space there)
OUTPUT_BASE = Path(f'/Volumes/TOSHIBA EXT/map/{COUNTRY}')
OUTPUT_GPKG = OUTPUT_BASE / f"{COUNTRY}_affordability_analysis.gpkg"

# Parameters
PROJECT_LIFETIME_YEARS = 20
DISCOUNT_RATE = 0.10  # 10% discount rate

# Currency conversion: Income data is in USD (2011 PPP), costs are in EUR
# Current EUR/USD exchange rate (Dec 2025): 1.17
# This means: 1 EUR = 1.17 USD, or 1 USD = 0.855 EUR
USD_TO_EUR = 1 / 1.17  # 0.855

# HDI-based household tier thresholds (from advanced_sample_generator_v2.py)
HDI_CSV = '/Users/matteo/Desktop/Tesi/Data sheet/hdi_values.csv'
hdi_df = pd.read_csv(HDI_CSV)
hdi_dict = dict(zip(hdi_df['country'], hdi_df['hdi_value']))
HDI = hdi_dict[COUNTRY]

HDI_THRESHOLDS = [
    (0.8, [0.05, 0.15, 0.30, 0.40, 0.10]),  # Very high HDI
    (0.7, [0.10, 0.20, 0.35, 0.30, 0.05]),  # High HDI
    (0.55, [0.20, 0.30, 0.30, 0.15, 0.05]), # Medium HDI
    (0.0, [0.30, 0.35, 0.25, 0.08, 0.02])   # Low HDI
]

def get_tier_distribution(hdi):
    """Get household tier distribution based on HDI (fallback only)."""
    for threshold, distribution in HDI_THRESHOLDS:
        if hdi >= threshold:
            return distribution
    return HDI_THRESHOLDS[-1][1]


def discover_ssa_countries(outputs_country_dir: Path) -> List[str]:
    """Discover all SSA country ISO codes that have a sample CSV."""
    if not outputs_country_dir.exists():
        return []
    return sorted({csv_file.stem.split('_')[0] for csv_file in outputs_country_dir.glob('*_sample.csv')})


def load_country_outline(country: str):
    """Load the country outline for plotting if available."""
    gadm_path = Path('/Users/matteo/Desktop/Tesi/GADM') / f'gadm41_{country}.gpkg'
    if not gadm_path.exists():
        return None

    try:
        try:
            outline = gpd.read_file(gadm_path, layer='ADM_ADM_0')
        except Exception:
            outline = gpd.read_file(gadm_path)
        return outline.to_crs('EPSG:4326')
    except Exception:
        return None


def plot_affordability_panel(ax, gdf, value_col, title, country_outline=None):
    """Plot a single affordability panel using the standard three bins."""
    bins = [0, 5, 10, np.inf]
    # plot order: first cluster-level no-data (light grey), then red, yellow, green
    labels = ['green', 'yellow', 'red']
    color_map = {
        'green': WI_COLOR_SCHEME.get('green_soft', '#7CFC00'),
        'yellow': WI_COLOR_SCHEME.get('yellow_soft', '#FFE066'),
        'red': WI_COLOR_SCHEME.get('red_soft', '#FF6666'),
        # lighter grey for individual clusters with no data
        'no_data': '#DDDDDD',
    }

    cut = pd.cut(gdf[value_col], bins=bins, labels=labels, include_lowest=True)
    bin_col = f'{value_col}_bin'
    gdf[bin_col] = cut.astype(object)
    gdf.loc[gdf[value_col].isna(), bin_col] = 'no_data'

    if country_outline is not None:
        # Draw country boundary below points but above background so borders remain visible
        country_outline.plot(ax=ax, color='none', edgecolor='black', linewidth=0.8, zorder=2)
        minx, miny, maxx, maxy = country_outline.total_bounds
        xbuf = (maxx - minx) * 0.02
        ybuf = (maxy - miny) * 0.02
        ax.set_xlim(minx - xbuf, maxx + xbuf)
        ax.set_ylim(miny - ybuf, maxy + ybuf)

    # Plot cluster-level no-data first (lighter grey, below colored points)
    missing = gdf[gdf[bin_col] == 'no_data']
    if not missing.empty:
        xs = missing.geometry.x.values
        ys = missing.geometry.y.values
        ax.scatter(xs, ys, s=1.2, c=color_map['no_data'], alpha=0.9, linewidths=0, zorder=8)

    # Then plot the colored bins in order red -> yellow -> green
    # Use the weighted-index map point style: slightly translucent, small size
    for label in labels:
        subset = gdf[gdf[bin_col] == label]
        if not subset.empty:
            xs = subset.geometry.x.values
            ys = subset.geometry.y.values
            ax.scatter(xs, ys, s=1.2, c=color_map[label], alpha=0.24, linewidths=0, zorder=10)

    ax.set_title(title, fontsize=10)
    ax.set_axis_off()


def render_ssa_mosaic(country_gdfs, output_dir: Path):
    """Render the requested 3x3 SSA mosaic figure."""
    if not country_gdfs:
        print("No SSA data available for mosaic rendering.")
        return None

    ssa_gdf = pd.concat(country_gdfs, ignore_index=True)
    ssa_gdf = gpd.GeoDataFrame(ssa_gdf, geometry='geometry', crs='EPSG:4326')

    # Build one SSA outline from all available country outlines.
    outlines = []
    for iso_code in sorted({str(value) for value in ssa_gdf['country'] if pd.notna(value)}):
        outline = load_country_outline(iso_code)
        if outline is not None:
            outlines.append(outline)
    country_outline = pd.concat(outlines, ignore_index=True) if outlines else None

    # Add explicit `no data` outlines for specific countries
    no_data_isos = ['SDN', 'SSD', 'ERI', 'SOM']  # Sudan, South Sudan, Eritrea, Somalia
    no_data_outlines = []
    for iso in no_data_isos:
        try:
            ol = load_country_outline(iso)
            if ol is not None:
                no_data_outlines.append((iso, ol))
        except Exception:
            continue

    # Rename the panel columns for clarity (use explicit household tier wording)
    panel_specs = [
        ('affordability_tier1_pct', 'Household tier 1 affordability'),
        ('affordability_tier2_pct', 'Household tier 2 affordability'),
        ('affordability_tier3_pct', 'Household tier 3 affordability'),
        ('affordability_tier4_pct', 'Household tier 4 affordability'),
        ('affordability_tier5_pct', 'Household tier 5 affordability'),
        ('cost_burden_pct', 'Cluster-level affordability'),
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    output_png = output_dir / 'ssa_affordability_mosaic.png'

    fig = plt.figure(figsize=(22, 18), dpi=200)
    gs = GridSpec(3, 4, figure=fig, width_ratios=[1, 1, 1, 0.9], wspace=0.04, hspace=0.02)

    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax3 = fig.add_subplot(gs[0, 2])
    ax4 = fig.add_subplot(gs[1:, 0:2])
    ax5 = fig.add_subplot(gs[1, 2])
    ax6 = fig.add_subplot(gs[2, 2])
    legend_ax = fig.add_subplot(gs[:, 3])

    plot_affordability_panel(ax1, ssa_gdf.copy(), panel_specs[0][0], panel_specs[0][1], country_outline)
    plot_affordability_panel(ax2, ssa_gdf.copy(), panel_specs[1][0], panel_specs[1][1], country_outline)
    plot_affordability_panel(ax3, ssa_gdf.copy(), panel_specs[2][0], panel_specs[2][1], country_outline)
    plot_affordability_panel(ax4, ssa_gdf.copy(), panel_specs[5][0], panel_specs[5][1], country_outline)
    plot_affordability_panel(ax5, ssa_gdf.copy(), panel_specs[3][0], panel_specs[3][1], country_outline)
    plot_affordability_panel(ax6, ssa_gdf.copy(), panel_specs[4][0], panel_specs[4][1], country_outline)

    # Add small summary stats to each panel (mean and % <5) when data available
    axes = [ax1, ax2, ax3, ax4, ax5, ax6]
    for ax, (col, title) in zip(axes, panel_specs):
        if col in ssa_gdf.columns:
            series = ssa_gdf[col]
            valid = series.dropna()
            if len(valid) > 0:
                mean_val = valid.mean()
                pct_affordable = (valid < 5).sum() / len(valid) * 100
                stats_text = f"mean {mean_val:.1f}%\n<5%: {pct_affordable:.1f}%"
                # place at lower-right inside panel
                ax.text(0.98, 0.02, stats_text, transform=ax.transAxes, fontsize=9, va='bottom', ha='right', bbox=dict(facecolor='white', alpha=0.6, edgecolor='none'))

    legend_ax.set_axis_off()
    legend_ax.set_xlim(0, 1)
    legend_ax.set_ylim(0, 1)

    # Legend order: dark grey (no-data country), red, yellow, green, light grey (no-data cluster)
    color_legend = [
        Patch(facecolor='#999999', edgecolor='black', label='No-data country (dark grey)'),
        Patch(facecolor=WI_COLOR_SCHEME.get('red_soft', '#FF6666'), edgecolor='black', label='Red: affordability >= 10%'),
        Patch(facecolor=WI_COLOR_SCHEME.get('yellow_soft', '#FFE066'), edgecolor='black', label='Yellow: 5% to < 10%'),
        Patch(facecolor=WI_COLOR_SCHEME.get('green_soft', '#7CFC00'), edgecolor='black', label='Green: affordability < 5%'),
        Patch(facecolor='#DDDDDD', edgecolor='black', label='No-data cluster (light grey)'),
    ]

    legend_ax.text(0.0, 0.98, 'Legend', fontsize=18, fontweight='bold', va='top')
    legend_ax.text(0.0, 0.90, 'Bin rules', fontsize=13, fontweight='bold', va='top')
    legend_ax.legend(
        handles=color_legend,
        loc='upper left',
        bbox_to_anchor=(0.0, 0.86),
        frameon=False,
        fontsize=11,
        borderaxespad=0.0,
        handlelength=1.6,
        handleheight=1.2,
    )

    # Removed panel-layout explanatory block per user request.
    legend_ax.text(
        0.0,
        0.22,
        'Thresholds are created with pd.cut:\n[0, 5, 10, inf]\nThis means:\n< 5% = green\n5% to < 10% = yellow\n>= 10% = red\nNaN = grey',
        fontsize=11,
        va='top',
        linespacing=1.45,
    )

    fig.suptitle('SSA Affordability Mosaic', fontsize=16, y=0.98)
    # Plot `no data` country outlines (grey fill) if present
    try:
        # First, fill no-data countries with grey UNDER the clusters (low zorder)
        for iso, ol in no_data_outlines:
            for ax in fig.axes:
                try:
                    # darker grey fill for entire country polygons with no data
                    ol.plot(ax=ax, color='#999999', edgecolor='none', linewidth=0.0, zorder=1)
                except Exception:
                    continue

        # Then draw country outlines (slightly thicker) so borders are visible even where there are no clusters
        if country_outline is not None:
            for ax in fig.axes:
                try:
                    # draw a more prominent Africa outline (still below clusters)
                    country_outline.boundary.plot(ax=ax, color='black', linewidth=1.2, zorder=3)
                except Exception:
                    continue
    except Exception:
        pass

    plt.savefig(output_png, bbox_inches='tight', dpi=200)
    plt.close(fig)
    print(f"Saved SSA mosaic to: {output_png}")
    return output_png

# ============================================================================
# INCOME DISTRIBUTION BY TIER
# ============================================================================

def create_income_distribution_by_tier(mean_income_pc, gini, household_size, tier_distribution):
    """Pareto-lognormal income by tier (uses regional Gini)."""
    if pd.isna(mean_income_pc) or mean_income_pc == 0 or pd.isna(gini):
        return [np.nan] * 5

    gini = np.clip(gini, 0.2, 0.8)
    cumulative_pct = np.cumsum([0] + tier_distribution)

    tier_incomes_pc = []
    for i in range(5):
        lower_pct = cumulative_pct[i]
        upper_pct = cumulative_pct[i + 1]
        median_pct = (lower_pct + upper_pct) / 2
        try:
            tier_income_pc = wealth_combined(median_pct, mean_income_pc, gini, gamma=0.32)
            tier_income_pc = max(0, tier_income_pc)
        except Exception:
            tier_income_pc = np.nan
        tier_incomes_pc.append(tier_income_pc)

    return [income_pc * household_size for income_pc in tier_incomes_pc]

# ============================================================================
# DEMAND EXTRACTION
# ============================================================================

def get_demand_by_tier(row):
    """
    Calculate Year 1 demand by household tier using MGPY archetypes.
    
    Call demand_calculation ONCE with all household counts, then extract
    total demand per tier and divide by household count to get per-household demand.
    
    Returns dict: {
        1: {'per_hh_kwh': kwh/household, 'num_hh': count},
        2: {...},
        ...
    }
    
    Args:
        row: pandas Series from sample CSV with columns lat, cooling, h_tier1-5, schools, hospital_X
    """
    try:
        # Extract required parameters from row
        lat = float(row.get('lat', np.nan))
        cooling_period = row.get('cooling', None)
        
        # Handle missing cooling period: check for None, NaN, empty string
        if cooling_period is None or pd.isna(cooling_period) or cooling_period == '':
            cooling_period = 'NC'  # Default to No cooling (matches YAML template default)
        else:
            cooling_period = str(cooling_period).strip()
        
        if pd.isna(lat):
            return {i: {'per_hh_kwh': np.nan, 'num_hh': 0} for i in range(1, 6)}
        
        # Household tier counts
        h_tier1 = float(row.get('h_tier1', 0) or 0)
        h_tier2 = float(row.get('h_tier2', 0) or 0)
        h_tier3 = float(row.get('h_tier3', 0) or 0)
        h_tier4 = float(row.get('h_tier4', 0) or 0)
        h_tier5 = float(row.get('h_tier5', 0) or 0)
        
        tier_counts = {1: h_tier1, 2: h_tier2, 3: h_tier3, 4: h_tier4, 5: h_tier5}
        
        # Hospital counts (per tier)
        h_hosp1 = float(row.get('hospital_1', 0) or 0)
        h_hosp2 = float(row.get('hospital_2', 0) or 0)
        h_hosp3 = float(row.get('hospital_3', 0) or 0)
        h_hosp4 = float(row.get('hospital_4', 0) or 0)
        h_hosp5 = float(row.get('hospital_5', 0) or 0)
        
        # Schools and demand growth (use defaults if not present)
        num_schools = float(row.get('schools', 0) or 0)
        demand_growth = 0.02  # 2% annual growth
        
        # Standard parameters (fixed for all clusters)
        years = 1  # Only Year 1
        periods = 24  # Hourly resolution
        
        # Check if there's any load at all
        total_loads = sum(tier_counts.values()) + h_hosp1 + h_hosp2 + h_hosp3 + h_hosp4 + h_hosp5 + num_schools
        if total_loads == 0:
            return {i: {'per_hh_kwh': 0.0, 'num_hh': 0} for i in range(1, 6)}
        
        # Try to use precomputed single-household archetype templates per tier
        base = get_archetype_base(lat, cooling_period, years=years, periods=periods)
        # If any tier with households has no base value, fall back to full aggregated calculation
        fallback_needed = False
        for tier in range(1, 6):
            if tier_counts[tier] > 0 and (tier not in base or pd.isna(base.get(tier))):
                fallback_needed = True
                break

        if fallback_needed:
            # Fall back to original aggregated demand_calculation call
            try:
                load_total, users = cached_demand_calculation(
                    lat=lat,
                    cooling_period=cooling_period,
                    h_tier1=h_tier1,
                    h_tier2=h_tier2,
                    h_tier3=h_tier3,
                    h_tier4=h_tier4,
                    h_tier5=h_tier5,
                    num_schools=0,
                    h_hosp1=0,
                    h_hosp2=0,
                    h_hosp3=0,
                    h_hosp4=0,
                    h_hosp5=0,
                    demand_growth=demand_growth,
                    years=years,
                    periods=periods
                )
            except ValueError as e:
                if "Total load is zero" in str(e):
                    return {i: {'per_hh_kwh': 0.0, 'num_hh': 0} for i in range(1, 6)}
                else:
                    raise

            tier_demands_kwh = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
            for user in users:
                user_name = user.name
                if 'Household_Tier_' in user_name:
                    tier = int(user_name.replace('Household_Tier_', ''))
                    year1_demand = user.demand_data['Year_1'].sum()
                    tier_demands_kwh[tier] += float(year1_demand)

            result = {}
            for tier in range(1, 6):
                num_hh = tier_counts[tier]
                if num_hh > 0 and tier_demands_kwh[tier] > 0:
                    per_hh_kwh = (tier_demands_kwh[tier] / num_hh) / 1000
                    result[tier] = {'per_hh_kwh': per_hh_kwh, 'num_hh': int(num_hh)}
                else:
                    result[tier] = {'per_hh_kwh': np.nan, 'num_hh': int(num_hh)}

            return result

        # Use base templates: base[tier] already in kWh per household
        result = {}
        for tier in range(1, 6):
            num_hh = tier_counts[tier]
            per_hh_kwh = base.get(tier, np.nan)
            if num_hh > 0 and pd.notna(per_hh_kwh):
                result[tier] = {'per_hh_kwh': per_hh_kwh, 'num_hh': int(num_hh)}
            else:
                # No households in this tier -> preserve original behaviour
                result[tier] = {'per_hh_kwh': np.nan, 'num_hh': int(num_hh)}

        return result

    except Exception as e:
        # Log error and return NaNs for this cluster
        import traceback
        print(f"Error in get_demand_by_tier for {row.get('category', 'unknown')}: {e}")
        traceback.print_exc()
        return {i: {'per_hh_kwh': np.nan, 'num_hh': 0} for i in range(1, 6)}

# ============================================================================
# MAIN PROCESSING
# ============================================================================

def main(projects_dir: str = None, output_dir: str = None, country: str = None, test_mode: bool = False, export_outputs: bool = True, resume: bool = False, checkpoint_interval: int = 100):
    print(f"="*80)
    global PROJECTS_DIR, SAMPLE_CSV, INCOME_CSV, OUTPUT_GPKG, OUTPUT_BASE, COUNTRY

    outputs_country_dir = Path('/Volumes/TOSHIBA EXT/OUTPUTS COUNTRY')
    if country is None or str(country).upper() == 'ALL':
        countries = discover_ssa_countries(outputs_country_dir)
        if not countries:
            print(f"No country CSV files found in {outputs_country_dir}")
            return

        ssa_output_dir = Path(output_dir) if output_dir else SSA_OUTPUT_ROOT
        print(f"Running affordability analysis for {len(countries)} SSA countries")
        print(f"SSA output root: {ssa_output_dir}")

        country_gdfs = []
        for iso_code in countries:
            print(f"\n>>> Processing {iso_code} <<<")
            country_gdf = main(
                projects_dir=projects_dir,
                output_dir=str(ssa_output_dir),
                country=iso_code,
                test_mode=test_mode,
                export_outputs=False,
            )
            if country_gdf is not None:
                country_gdfs.append(country_gdf)

        render_ssa_mosaic(country_gdfs, Path(ssa_output_dir) / 'Affordability')

        print(f"\nCompleted SSA-wide affordability analysis for {len(countries)} countries")
        return

    if country:
        COUNTRY = country
    if projects_dir:
        PROJECTS_DIR = Path(projects_dir)

    # Locate sample CSV: prefer OUTPUTS COUNTRY, then PROJECTS_DIR
    SAMPLE_CSV = outputs_country_dir / f"{COUNTRY}_sample.csv"
    if not SAMPLE_CSV.exists():
        SAMPLE_CSV = PROJECTS_DIR / "sample.csv"
        if not SAMPLE_CSV.exists():
            candidate = PROJECTS_DIR / f"{COUNTRY}_sample.csv"
            if candidate.exists():
                SAMPLE_CSV = candidate
            else:
                found = list(PROJECTS_DIR.glob('*_sample.csv'))
                SAMPLE_CSV = found[0] if found else SAMPLE_CSV

    # Locate income CSV: prefer OUTPUTS COUNTRY, then PROJECTS_DIR
    INCOME_CSV = outputs_country_dir / f"{COUNTRY}_clusters_income.csv"
    if not INCOME_CSV.exists():
        INCOME_CSV = PROJECTS_DIR / f"{COUNTRY}_clusters_income.csv"
        if not INCOME_CSV.exists():
            found_inc = list(PROJECTS_DIR.glob('*_clusters_income.csv'))
            if found_inc:
                # try to pick file matching country prefix if present
                pref = [p for p in found_inc if p.name.startswith(f'{COUNTRY}_')]
                INCOME_CSV = pref[0] if pref else found_inc[0]
    
    # Output always to Toshiba (no other location)
    if output_dir:
        OUTPUT_BASE = Path(output_dir) / COUNTRY
    else:
        OUTPUT_BASE = Path(f'/Volumes/TOSHIBA EXT/map/{COUNTRY}')
    
    OUTPUT_GPKG = OUTPUT_BASE / f"{COUNTRY}_affordability_analysis.gpkg"

    # Checkpoint path for incremental progress
    checkpoint_path = OUTPUT_BASE / f"{COUNTRY}_affordability_progress.csv"

    print(f"AFFORDABILITY ANALYSIS - {COUNTRY}")
    if test_mode:
        print(f"[TEST MODE] Processing first 50 clusters only")
    print(f"="*80)
    
    # Load data
    print("\nLoading data...")
    df_opt = pd.read_csv(SAMPLE_CSV)
    df_income = pd.read_csv(INCOME_CSV)
    df_hh_size = pd.read_csv(HOUSEHOLD_SIZE_CSV)
    
    # Apply test mode if requested
    if test_mode:
        df_opt = df_opt.head(50)
        print(f"[TEST MODE] Limited to first 50 clusters")

    # Enforce using `income_nominal_pc` as the source of income values
    if 'income_nominal_pc' in df_income.columns:
        df_income['income_pc'] = df_income['income_nominal_pc']
        print('Using income_nominal_pc for income values')
    else:
        print('ERROR: income_nominal_pc column not found in', INCOME_CSV)
        print('Run `income_add.py` to generate nominal incomes, or add the column to the CSV.')
        sys.exit(1)

    # Ensure `gni` exists to avoid KeyError later
    if 'gni' not in df_income.columns:
        df_income['gni'] = pd.NA
    
    print(f"Optimization output: {len(df_opt)} clusters")
    print(f"Income data: {len(df_income)} clusters")
    
    # Merge income data - only use columns that exist in the CSV
    income_cols_to_merge = ['cat', 'type', 'gini', 'region']
    if 'income_nominal_pc' in df_income.columns:
        income_cols_to_merge.append('income_nominal_pc')
    if 'income_ppp_pc' in df_income.columns:
        income_cols_to_merge.append('income_ppp_pc')
    
    df = df_opt.merge(df_income[income_cols_to_merge], 
                      on='cat', how='left')
    
    print(f"Merged: {len(df)} clusters")
    print(f"Clusters with income_nominal_pc: {df['income_nominal_pc'].notna().sum() if 'income_nominal_pc' in df.columns else 0}")
    
    # Get household size for country
    hh_size_data = df_hh_size[df_hh_size['Country Code'] == COUNTRY]
    if len(hh_size_data) == 0:
        print(f"Warning: No household size data for {COUNTRY}, using default 5.0")
        household_size_national = 5.0
        household_size_urban = 5.0
        household_size_rural = 5.0
    else:
        household_size_national = hh_size_data['Mean national'].iloc[0]
        household_size_urban = hh_size_data['Mean urban'].iloc[0]
        household_size_rural = hh_size_data['Mean rural'].iloc[0]
    
    print(f"Household size: National={household_size_national:.1f}, Urban={household_size_urban:.1f}, Rural={household_size_rural:.1f}")
    
    # Get tier distribution from actual h_tier counts (RWI-based); fallback to HDI if missing
    total_households = df_opt['h_tier1'].sum() + df_opt['h_tier2'].sum() + df_opt['h_tier3'].sum() + df_opt['h_tier4'].sum() + df_opt['h_tier5'].sum()
    if total_households > 0:
        tier_dist = [
            df_opt['h_tier1'].sum() / total_households,
            df_opt['h_tier2'].sum() / total_households,
            df_opt['h_tier3'].sum() / total_households,
            df_opt['h_tier4'].sum() / total_households,
            df_opt['h_tier5'].sum() / total_households,
        ]
        print(f"RWI-based tier distribution: {[f'{x*100:.1f}%' for x in tier_dist]}")
    else:
        tier_dist = get_tier_distribution(HDI)
        print(f"HDI={HDI:.3f} → Tier distribution (fallback): {tier_dist}")
    
    # Initialize new columns for Method 1 (household affordability by tier)
    for i in range(1, 6):
        df[f'demand_tier{i}_kwh'] = np.nan
        df[f'income_tier{i}_hh'] = np.nan
        df[f'elec_cost_tier{i}_hh'] = np.nan
        df[f'affordability_tier{i}_pct'] = np.nan
    
    # Initialize columns for Method 2 (community cost burden)
    df['total_annual_cost'] = np.nan
    df['cost_per_capita_annual'] = np.nan
    df['cost_burden_pct'] = np.nan
    
    # Filter out schools before processing (if column exists)
    if 'type' in df.columns:
        before_filter = len(df)
        df = df[df['type'] != 'school'].copy()
        print(f"After removing schools: {len(df)} clusters (removed {before_filter - len(df)})")
    else:
        print("No 'type' column found; skipping school-type filtering")

    # Additionally, for COD we want to discard hospital clusters (treat like schools)
    if str(COUNTRY).upper() == 'COD':
        hosp_cols = [c for c in df.columns if c.startswith('hospital_')]
        if hosp_cols:
            before_hosp = len(df)
            # Keep rows where all hospital counts are zero or NaN
            df = df[df[hosp_cols].fillna(0).sum(axis=1) == 0].copy()
            print(f"After removing hospital clusters for COD: {len(df)} clusters (removed {before_hosp - len(df)})")
        else:
            print("No hospital_* columns found to filter for COD")
    # Reset index so we can build column lists in order and assign in bulk
    df = df.reset_index(drop=True)
    # If resuming, remove already-processed clusters using checkpoint file
    if resume and checkpoint_path.exists():
        try:
            cp = pd.read_csv(checkpoint_path)
            processed = set(cp['cat'].astype(str).tolist())
            before = len(df)
            df = df[~df['cat'].astype(str).isin(processed)].reset_index(drop=True)
            after = len(df)
            print(f"Resuming from checkpoint: skipped {before-after} already-processed clusters; {after} remaining")
        except Exception as e:
            print(f"Could not read checkpoint file {checkpoint_path}: {e}")
    
    # Process each cluster with buffered checkpointing to avoid losing progress
    print("\nProcessing clusters with checkpointing...")
    pending = []
    processed_count = 0
    n = len(df)

    # Prepare a stable column order for checkpoint files to avoid CSV corruption
    computed_cols = []
    for i in range(1, 6):
        computed_cols += [
            f'demand_tier{i}_kwh',
            f'elec_cost_tier{i}_hh',
            f'income_tier{i}_hh',
            f'affordability_tier{i}_pct',
        ]
    computed_cols += ['total_annual_cost', 'cost_per_capita_annual', 'cost_burden_pct']
    final_columns = list(df.columns) + [c for c in computed_cols if c not in df.columns]

    def flush_pending():
        nonlocal pending
        if not pending:
            return
        chunk_df = pd.DataFrame(pending)
        # Reindex to stable column order (missing columns will be created with NaN)
        try:
            chunk_df = chunk_df.reindex(columns=final_columns)
        except Exception:
            # fallback: ensure at least columns align for writing
            pass
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        header = not checkpoint_path.exists()
        try:
            chunk_df.to_csv(checkpoint_path, mode='a', index=False, header=header)
        except Exception as e:
            print(f"Failed to write checkpoint to {checkpoint_path}: {e}")
        pending = []

    for pos, (_, row) in enumerate(tqdm(df.iterrows(), total=n, desc="Calculating affordability")):
        # Build result dict starting from original row values
        res = row.to_dict()

        cluster_type = row['type']
        if cluster_type == 'urban':
            hh_size = household_size_urban
        elif cluster_type == 'rural':
            hh_size = household_size_rural
        else:
            hh_size = household_size_national

        income_pc = row.get('income_nominal_pc', np.nan)
        gini = row.get('gini', np.nan)

        # initialize outputs for tiers
        for i in range(1, 6):
            res[f'demand_tier{i}_kwh'] = np.nan
            res[f'elec_cost_tier{i}_hh'] = np.nan
            res[f'income_tier{i}_hh'] = np.nan
            res[f'affordability_tier{i}_pct'] = np.nan

        res['total_annual_cost'] = np.nan
        res['cost_per_capita_annual'] = np.nan
        res['cost_burden_pct'] = np.nan

        if pd.notna(income_pc):
            tier_incomes_hh = create_income_distribution_by_tier(income_pc, gini, hh_size, tier_dist)
            tier_demands = get_demand_by_tier(row)
            lcoe = row['lcoe (€/kWh)']

            for i in range(1, 6):
                demand_data = tier_demands[i]
                per_hh_kwh = demand_data['per_hh_kwh']
                num_households = demand_data['num_hh']
                if pd.notna(per_hh_kwh) and per_hh_kwh > 0 and num_households > 0:
                    res[f'demand_tier{i}_kwh'] = per_hh_kwh
                    elec_cost_hh = lcoe * per_hh_kwh
                    res[f'elec_cost_tier{i}_hh'] = elec_cost_hh
                    income_hh_usd = tier_incomes_hh[i - 1]
                    income_hh_eur = income_hh_usd * USD_TO_EUR
                    res[f'income_tier{i}_hh'] = income_hh_eur
                    res[f'affordability_tier{i}_pct'] = (elec_cost_hh / income_hh_eur * 100) if income_hh_eur > 0 else np.nan

        # Method 2
        try:
            capital_cost = row['capital_cost (k€)'] * 1000
            om_cost = row['o_and_m_cost (k€)'] * 1000
            total_annual_cost = (capital_cost / PROJECT_LIFETIME_YEARS) + om_cost
            res['total_annual_cost'] = total_annual_cost
            population = row.get('pop_sum', 0)
            if population > 0:
                cost_per_capita = total_annual_cost / population
                res['cost_per_capita_annual'] = cost_per_capita
                income_nominal_pc_row = row.get('income_nominal_pc', np.nan)
                if pd.notna(income_nominal_pc_row) and income_nominal_pc_row > 0:
                    income_pc_eur = income_nominal_pc_row * USD_TO_EUR
                    res['cost_burden_pct'] = (cost_per_capita / income_pc_eur * 100)
        except Exception:
            pass

        pending.append(res)
        processed_count += 1

        # Flush periodically
        if processed_count % checkpoint_interval == 0:
            flush_pending()

    # Final flush
    flush_pending()

    # After processing, load final results from checkpoint (or fall back to df if not saved)
    if checkpoint_path.exists():
        try:
            final_df = pd.read_csv(checkpoint_path)
        except Exception as e:
            print(f"Could not read final checkpoint {checkpoint_path}: {e}")
            final_df = df.copy()
    else:
        # No checkpoint file created (e.g., zero clusters); use df with NaNs
        final_df = df.copy()

    # Use final_df for downstream plotting and summary
    # Use final_df GeoDataFrame for downstream plotting and summary
    gdf = gpd.GeoDataFrame(final_df, geometry=[Point(lon, lat) for lon, lat in zip(final_df['lon'], final_df['lat'])], crs="EPSG:4326")
    gdf['country'] = COUNTRY

    # Diagnostic: Check tier demand population using the saved final dataframe
    print("\n[DIAGNOSTIC] Tier demand status:")
    for tier in range(1, 6):
        col = f'demand_tier{tier}_kwh'
        populated = final_df[col].notna().sum() if col in final_df.columns else 0
        total = len(final_df)
        pct = (populated / total * 100) if total > 0 else 0.0
        print(f"  Tier {tier}: {populated}/{total} clusters with demand ({pct:.1f}%)")
    
    # Do not save GPKG by default (user requested cancellation).
    print(f"\nSkipping GPKG save as requested. (would have been: {OUTPUT_GPKG})")

    if export_outputs:
        # Instead export a CSV of results and create PNG maps (including tiered maps).
        try:
            out_base = OUTPUT_BASE
            out_base.mkdir(parents=True, exist_ok=True)

            csv_out = out_base / f'{COUNTRY}_affordability_analysis.csv'
            gdf.drop(columns='geometry', errors='ignore').to_csv(csv_out, index=False)
            print(f"Saved CSV: {csv_out}")

            # Load country outline if available and plot tiered PNGs
            gadm_path = Path('/Users/matteo/Desktop/Tesi/GADM') / f'gadm41_{COUNTRY}.gpkg'
            country_outline = None
            try:
                if gadm_path.exists():
                    # prefer admin 0 layer if available
                    try:
                        country_outline = gpd.read_file(gadm_path, layer='ADM_ADM_0')
                    except Exception:
                        # fallback to default
                        country_outline = gpd.read_file(gadm_path)
                    country_outline = country_outline.to_crs('EPSG:4326')
            except Exception:
                country_outline = None

            # Tiered PNGs: per-household-tier affordability
            png_dir = out_base / 'Affordability'
            png_dir.mkdir(parents=True, exist_ok=True)

            # Method 2: Cost burden map with explicit 'no_data' handling
            try:
                bins = [0, 5, 10, np.inf]
                labels = ['green', 'yellow', 'red']
                # Do not coerce NaN to inf; create bin series then mark NaNs as 'no_data'
                cut = pd.cut(gdf['cost_burden_pct'], bins=bins, labels=labels, include_lowest=True)
                gdf['cost_burden_bin'] = cut.astype(object)
                gdf.loc[gdf['cost_burden_pct'].isna(), 'cost_burden_bin'] = 'no_data'

                color_map = {'green': '#7CFC00', 'yellow': '#FFE066', 'red': '#FF6666', 'no_data': '#CCCCCC'}
                fig, ax = plt.subplots(1, 1, figsize=(12, 9), dpi=150)
                # Plot country outline first (base layer)
                if country_outline is not None:
                    country_outline.plot(ax=ax, color='none', edgecolor='black', linewidth=1, zorder=0)
                    # set extent to country bounds with small buffer
                    minx, miny, maxx, maxy = country_outline.total_bounds
                    xbuf = (maxx - minx) * 0.02
                    ybuf = (maxy - miny) * 0.02
                    ax.set_xlim(minx - xbuf, maxx + xbuf)
                    ax.set_ylim(miny - ybuf, maxy + ybuf)

                # Plot bins (exclude 'no_data' to remove grey points)
                for label in labels:
                    subset = gdf[gdf['cost_burden_bin'] == label]
                    if not subset.empty:
                        subset.plot(ax=ax, color=color_map[label], markersize=1.6, label=f"{label} ({len(subset)})", zorder=10)

                ax.legend()
                ax.set_title(f"Affordability - Cost burden (%) - {COUNTRY}")
                out_png = png_dir / f'affordability_cost_burden_{COUNTRY}.png'
                plt.tight_layout()
                plt.savefig(out_png, bbox_inches='tight', dpi=150)
                plt.close()
                print(f"Saved affordability PNG to {out_png}")
            except Exception as e:
                print(f"Could not create affordability PNG: {e}")

            # Create tiered affordability PNGs for tiers 1..5 with explicit 'no_data'
            for i in range(1, 6):
                col = f'affordability_tier{i}_pct'
                if col not in gdf.columns:
                    continue
                try:
                    labels = ['green', 'yellow', 'red']
                    bins = [0, 5, 10, np.inf]
                    cut = pd.cut(gdf[col], bins=bins, labels=labels, include_lowest=True)
                    gdf[f'tier{i}_bin'] = cut.astype(object)
                    gdf.loc[gdf[col].isna(), f'tier{i}_bin'] = 'no_data'

                    fig, ax = plt.subplots(1, 1, figsize=(12, 9), dpi=150)
                    if country_outline is not None:
                        country_outline.plot(ax=ax, color='none', edgecolor='black', linewidth=1, zorder=0)
                        minx, miny, maxx, maxy = country_outline.total_bounds
                        xbuf = (maxx - minx) * 0.02
                        ybuf = (maxy - miny) * 0.02
                        ax.set_xlim(minx - xbuf, maxx + xbuf)
                        ax.set_ylim(miny - ybuf, maxy + ybuf)

                    # plot bins (exclude 'no_data' to remove grey points)
                    for label in labels:
                        subset = gdf[gdf[f'tier{i}_bin'] == label]
                        if not subset.empty:
                            subset.plot(ax=ax, color=color_map[label], markersize=1.6, label=f"{label} ({len(subset)})", zorder=10)

                    ax.legend()
                    ax.set_title(f"Affordability Tier {i} (%) - {COUNTRY}")
                    out_png = png_dir / f'affordability_tier{i}_{COUNTRY}.png'
                    plt.tight_layout()
                    plt.savefig(out_png, bbox_inches='tight', dpi=150)
                    plt.close()
                    print(f"Saved tier PNG to {out_png}")
                except Exception as e:
                    print(f"Could not create PNG for tier {i}: {e}")

        except Exception as e:
            print(f"Could not export CSV/PNGs: {e}")

        # Also export a simple PNG map of cost burden bins if output_dir provided
        try:
            if output_dir:
                png_out_dir = Path(output_dir) / COUNTRY / 'Affordability'
            else:
                png_out_dir = Path('/Users/matteo/Desktop') / 'map' / COUNTRY / 'Affordability'
            png_out_dir.mkdir(parents=True, exist_ok=True)

            # Create bins
            bins = [0, 5, 10, np.inf]
            labels = ['green', 'yellow', 'red']
            gdf['cost_burden_bin'] = pd.cut(gdf['cost_burden_pct'].fillna(np.inf), bins=bins, labels=labels, include_lowest=True)

            color_map = {'green': '#7CFC00', 'yellow': '#FFE066', 'red': '#FF6666'}
            fig, ax = plt.subplots(1, 1, figsize=(12, 9), dpi=150)
            for label in labels:
                subset = gdf[gdf['cost_burden_bin'] == label]
                if not subset.empty:
                    subset.plot(ax=ax, color=color_map[label], markersize=1.6, label=f"{label} ({len(subset)})")
            ax.legend()
            ax.set_title(f"Affordability - Cost burden (%) - {COUNTRY}")
            out_png = png_out_dir / f'affordability_cost_burden_{COUNTRY}.png'
            plt.tight_layout()
            plt.savefig(out_png, bbox_inches='tight', dpi=150)
            plt.close()
            print(f"Saved affordability PNG to {out_png}")
        except Exception as e:
            print(f"Could not create affordability PNG: {e}")
    
    # Print summary statistics
    print("\n" + "="*80)
    print("SUMMARY STATISTICS")
    print("="*80)
    
    # Method 1 summary (tier 3 as representative middle tier)
    print("\nMethod 1: Household Affordability (Tier 3 - Middle Income)")
    tier3_affordable = gdf[gdf['affordability_tier3_pct'] < 5].shape[0]
    tier3_moderate = gdf[(gdf['affordability_tier3_pct'] >= 5) & (gdf['affordability_tier3_pct'] < 10)].shape[0]
    tier3_unaffordable = gdf[gdf['affordability_tier3_pct'] >= 10].shape[0]
    tier3_total = gdf['affordability_tier3_pct'].notna().sum()
    
    if tier3_total > 0:
        print(f"  Affordable (<5%):        {tier3_affordable} ({tier3_affordable/tier3_total*100:.1f}%)")
        print(f"  Moderate (5-10%):        {tier3_moderate} ({tier3_moderate/tier3_total*100:.1f}%)")
        print(f"  Unaffordable (>10%):     {tier3_unaffordable} ({tier3_unaffordable/tier3_total*100:.1f}%)")
        print(f"  Mean affordability:      {gdf['affordability_tier3_pct'].mean():.1f}%")
    
    # Method 2 summary
    print("\nMethod 2: Community Cost Burden (Per Capita)")
    cost_affordable = gdf[gdf['cost_burden_pct'] < 5].shape[0]
    cost_moderate = gdf[(gdf['cost_burden_pct'] >= 5) & (gdf['cost_burden_pct'] < 10)].shape[0]
    cost_unaffordable = gdf[gdf['cost_burden_pct'] >= 10].shape[0]
    cost_total = gdf['cost_burden_pct'].notna().sum()
    
    if cost_total > 0:
        print(f"  Affordable (<5%):        {cost_affordable} ({cost_affordable/cost_total*100:.1f}%)")
        print(f"  Moderate (5-10%):        {cost_moderate} ({cost_moderate/cost_total*100:.1f}%)")
        print(f"  Unaffordable (>10%):     {cost_unaffordable} ({cost_unaffordable/cost_total*100:.1f}%)")
        print(f"  Mean cost burden:        {gdf['cost_burden_pct'].mean():.1f}%")
    
    print("\n✅ Affordability analysis complete!")
    print(f"   Output: {OUTPUT_GPKG}")
    return gdf

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Affordability analysis runner')
    parser.add_argument('--projects-dir', help='Path to outputs_<COUNTRY>_advanced folder', default=None)
    parser.add_argument('--output-dir', help='Base output directory for GPKG and PNGs', default=None)
    parser.add_argument('--country', help='Country ISO (e.g., CIV) or ALL for SSA-wide processing', default='ALL')
    parser.add_argument('--test', action='store_true', help='Test mode: process only first 50 clusters')
    parser.add_argument('--resume', action='store_true', help='Resume from checkpoint CSV')
    parser.add_argument('--checkpoint-interval', type=int, default=100, help='Number of clusters to process before flushing checkpoint to disk')
    args = parser.parse_args()

    main(
        projects_dir=args.projects_dir,
        output_dir=args.output_dir,
        country=args.country,
        test_mode=args.test,
        resume=args.resume,
        checkpoint_interval=args.checkpoint_interval,
    )
