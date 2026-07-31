import pandas as pd
import geopandas as gpd
from pathlib import Path
from shapely.geometry import Point
from tqdm import tqdm

COUNTRY = "BEN"
PROJECTS_DIR = Path(f'/Volumes/2TB/outputs_{COUNTRY}_advanced')
SAMPLE_CSV = PROJECTS_DIR / "sample.csv"
RESULTS_GPKG = PROJECTS_DIR / "microgrid_results.gpkg"

# --- DEMAND BREAKDOWN COLUMN MAPPING ---
# Fill this dictionary with the mapping from demand_breakdown.csv columns to sample.csv columns
# Example:
DEMAND_BREAKDOWN_MAP = {
    "Household_Tier_1": "households_tier_1_demand (kWh)",
    "Household_Tier_2": "households_tier_2_demand (kWh)",
    "Household_Tier_3": "households_tier_3_demand (kWh)",
    "Household_Tier_4": "households_tier_4_demand (kWh)",
    "Household_Tier_5": "households_tier_5_demand (kWh)",
    "Hospital_Tier_1": "hospitals_1_demand (kWh)",
    "Hospital_Tier_2": "hospitals_2_demand (kWh)",
    "Hospital_Tier_3": "hospitals_3_demand (kWh)",
    "Hospital_Tier_4": "hospitals_4_demand (kWh)",
    "Hospital_Tier_5": "hospitals_5_demand (kWh)",
    "School": "schools_demand (kWh)",
    "Total": "aggregated_demand (kWh)"
}
# ---------------------------------------

def get_sizing_results(cat):
    """Get PV and Battery values from sizing_results.csv for a given cat"""
    sizing_file = PROJECTS_DIR / str(cat) / "results" / "sizing_results.csv"
    if not sizing_file.exists():
        print(f"No sizing results found for cat {cat}")
        return None, None
    try:
        df = pd.read_csv(sizing_file)
        pv_val = None
        battery_val = None
        pv_row = df[df['Component'].str.contains('Solar PV', case=False, na=False)]
        if not pv_row.empty:
            pv_val = pv_row['Total'].iloc[0]
        battery_row = df[df['Component'].str.contains('Battery', case=False, na=False)]
        if not battery_row.empty:
            battery_val = battery_row['Total'].iloc[0]
        return pv_val, battery_val
    except Exception as e:
        print(f"Error reading sizing results for cat {cat}: {e}")
        return None, None

def get_costs(cat):
    """Get cost values from costs.csv for a given cat"""
    costs_file = PROJECTS_DIR / str(cat) / "results" / "costs.csv"
    if not costs_file.exists():
        print(f"No costs found for cat {cat}")
        return None, None, None, None, None  # <-- Return 5 Nones
    try:
        df = pd.read_csv(costs_file)
        # Assumes columns: capital_cost, o_and_m_cost, battery_replacement_cost, lcoe, salvage_value
        row = df.iloc[0]
        return (
            row.get("capital_cost", None),
            row.get("o_and_m_cost", None),
            row.get("battery_replacement_cost", None),
            row.get("lcoe", None),
            row.get("salvage_value", None)
        )
    except Exception as e:
        print(f"Error reading costs for cat {cat}: {e}")
        return None, None, None, None, None  # <-- Return 5 Nones

def get_year1_demand(cat):
    """Get total Year 1 demand in kWh from Aggregated Demand.csv"""
    demand_file = PROJECTS_DIR / str(cat) / "inputs" / "demand files" / "Aggregated Demand.csv"
    if not demand_file.exists():
        print(f"No aggregated demand found for cat {cat}")
        return None
    try:
        df = pd.read_csv(demand_file)
        # Sum all values in Year_1 column and convert from Wh to kWh
        year1_total_wh = df['Year_1'].sum()
        year1_total_kwh = year1_total_wh / 1000
        return year1_total_kwh
    except Exception as e:
        print(f"Error reading aggregated demand for cat {cat}: {e}")
        return None

# def get_demand_breakdown(cat):
#     """Return a dict mapping sample.csv column names to summed demand values for this cluster."""
#     demand_file = PROJECTS_DIR / str(cat) / "inputs" / "demand files" / "demand_breakdown.csv"
#     if not demand_file.exists():
#         return {}
#     try:
#         df = pd.read_csv(demand_file)
#         sums = {}
#         for col in df.columns:
#             if col.lower() == "periods":
#                 continue
#             total = df[col].sum()
#             if total != 0 and col in DEMAND_BREAKDOWN_MAP:
#                 mapped_col = DEMAND_BREAKDOWN_MAP[col]
#                 sums[mapped_col] = total
#         return sums
#     except Exception as e:
#         print(f"Error reading demand_breakdown.csv for cat {cat}: {e}")
#         return {}

def main():
    if not SAMPLE_CSV.exists():
        print(f"Sample CSV not found at {SAMPLE_CSV}")
        return

    df = pd.read_csv(SAMPLE_CSV)

    orig_cols = [
        "cat","h_tier1","h_tier2","h_tier3","h_tier4","h_tier5",
        "hospital_1","hospital_2","hospital_3","hospital_4","hospital_5",
        "schools","school_total_demand","lat","lon","cooling","rwi"
    ]
    sizing_cost_cols = [
        "pv (kW)","battery (kWh)","capital_cost (k€)","o_and_m_cost (k€)",
        "lcoe (€/kWh)","salvage_value (k€)","demand_year1_kwh"
    ]
    # demand_order = [
    #     "households_tier_1_demand (kWh)",
    #     "households_tier_2_demand (kWh)",
    #     "households_tier_3_demand (kWh)",
    #     "households_tier_4_demand (kWh)",
    #     "households_tier_5_demand (kWh)",
    #     "hospitals_1_demand (kWh)",
    #     "hospitals_2_demand (kWh)",
    #     "hospitals_3_demand (kWh)",
    #     "hospitals_4_demand (kWh)",
    #     "hospitals_5_demand (kWh)",
    #     "schools_demand (kWh)",
    #     "aggregated_demand (kWh)"
    # ]

    # # Add demand columns if not present
    # for col in demand_order:
    #     if col not in df.columns:
    #         df[col] = None

    for idx, row in tqdm(df.iterrows(), total=len(df), desc="Processing clusters"):
        cat = row['cat']
        pv_val, battery_val = get_sizing_results(cat)
        if pv_val is not None:
            df.at[idx, 'pv (kW)'] = pv_val
        if battery_val is not None:
            df.at[idx, 'battery (kWh)'] = battery_val

        capital_cost, o_and_m_cost, battery_replacement_cost, lcoe, salvage_value = get_costs(cat)
        if capital_cost is not None:
            df.at[idx, 'capital_cost (k€)'] = capital_cost
        if o_and_m_cost is not None:
            df.at[idx, 'o_and_m_cost (k€)'] = o_and_m_cost
        if battery_replacement_cost is not None:
            df.at[idx, 'battery_replacement_cost (k€)'] = battery_replacement_cost
        if lcoe is not None:
            df.at[idx, 'lcoe (€/kWh)'] = lcoe
        if salvage_value is not None:
            df.at[idx, 'salvage_value (k€)'] = salvage_value
        
        # Get Year 1 demand
        year1_demand = get_year1_demand(cat)
        if year1_demand is not None:
            df.at[idx, 'demand_year1_kwh'] = year1_demand

        # Fill demand breakdown values using the mapping
        # demand_sums = get_demand_breakdown(cat)
        # for col in demand_order:
        #     df.at[idx, col] = demand_sums.get(col, None)

    # Only keep columns up to and including 'aggregated_demand (kWh)'
    # final_cols = orig_cols + sizing_cost_cols + demand_order
    # df = df.loc[:, final_cols]

    # csv
    df.to_csv(SAMPLE_CSV, index=False)

    # gpkg
    geometry = [Point(lon, lat) for lon, lat in zip(df['lon'], df['lat'])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
    gdf.to_file(RESULTS_GPKG, driver="GPKG")

if __name__ == "__main__":
    main()