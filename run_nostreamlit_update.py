# ---------- updated run_nostreamlit.py (modify your existing script to expose run_yaml) ----------
"""
This file is a refactor of your original run_nostreamlit.py to expose a programmatic
entrypoint `run_yaml(yaml_path)` so the orchestrator can call it in-process and avoid
spawning a separate Python interpreter for every simulation.

It keeps CLI compatibility (so you can still call it from the command line).
"""

import sys
from pathlib import Path
import argparse
import pandas as pd
import shutil
import numpy as np
import os

# ensure your package is on path if needed
sys.path.insert(0, "/Users/matteo/Desktop/MicroGridsPy-Development_Linopy-2")

from config.path_manager import PathManager
from microgridspy.model.parameters import ProjectParameters
from microgridspy.model.model import Model
from microgridspy.utils.archetypes import demand_calculation
from microgridspy.utils.pvgis import download_pvgis_pv_data
from microgridspy.post_process.export_results import save_energy_balance_to_excel, save_plots
from microgridspy.post_process.data_retrieval import (
    get_sizing_results,
    get_conversion_sizing_results,
    get_battery_soc,
    get_renewables_usage,
)
from microgridspy.post_process.cost_calculations import (
    calculate_actualized_investment_cost,
    calculate_lcoe,
    get_cost_details,
    calculate_actualized_salvage_value,
)
from microgridspy.post_process.energy_calculations import calculate_energy_usage


def run_yaml(yaml_path: str):
    """Programmatic entrypoint: run the whole pipeline for a single YAML file.
    This function should behave equivalently to running `python run_nostreamlit.py --yaml X`.
    """
    yaml_filepath = Path(yaml_path)
    project_folder = yaml_filepath.parent
    project_name = project_folder.name

    pm = PathManager(project_name)
    
    # Override BOTH instance AND class-level PathManager paths
    # The Model's initialize functions use class-level paths, not instance paths
    pm.INPUTS_FOLDER_PATH = project_folder / "inputs"
    pm.DEMAND_FOLDER_PATH = pm.INPUTS_FOLDER_PATH / "demand files"
    pm.AGGREGATED_DEMAND_FILE_PATH = pm.DEMAND_FOLDER_PATH / "Aggregated Demand.csv"
    pm.RESOURCE_FILE_PATH = pm.INPUTS_FOLDER_PATH / "Resources Availability.csv"
    
    # Also override class-level paths (critical for Model initialization)
    PathManager.INPUTS_FOLDER_PATH = pm.INPUTS_FOLDER_PATH
    PathManager.DEMAND_FOLDER_PATH = pm.DEMAND_FOLDER_PATH  
    PathManager.AGGREGATED_DEMAND_FILE_PATH = pm.AGGREGATED_DEMAND_FILE_PATH
    PathManager.RESOURCE_FILE_PATH = pm.RESOURCE_FILE_PATH
    PathManager.RES_COST_FILE_PATH = pm.INPUTS_FOLDER_PATH / 'RES Cost.csv'
    PathManager.BATTERY_COST_FILE_PATH = pm.INPUTS_FOLDER_PATH / 'Battery Cost.csv'
    PathManager.GRID_AVAILABILITY_FILE_PATH = pm.INPUTS_FOLDER_PATH / 'Grid Availability.csv'
    PathManager.TEMPERATURE_FILE_PATH = pm.INPUTS_FOLDER_PATH / 'Temperature.csv'
    PathManager.FUEL_SPECIFIC_COST_FILE_PATH = pm.INPUTS_FOLDER_PATH / 'Fuel Specific Cost.csv'
    
    pm.INPUTS_FOLDER_PATH.mkdir(parents=True, exist_ok=True)
    pm.DEMAND_FOLDER_PATH.mkdir(parents=True, exist_ok=True)

    settings = ProjectParameters.instantiate_from_yaml(yaml_filepath)

    # build demand params (unchanged logic)
    demand_params = {
        "lat": settings.resource_assessment.lat,
        "cooling_period": settings.archetypes_params.cooling_period,
        "num_h_tier1": settings.archetypes_params.h_tier1,
        "num_h_tier2": settings.archetypes_params.h_tier2,
        "num_h_tier3": settings.archetypes_params.h_tier3,
        "num_h_tier4": settings.archetypes_params.h_tier4,
        "num_h_tier5": settings.archetypes_params.h_tier5,
        "num_schools": settings.archetypes_params.schools,
        "num_hospitals1": settings.archetypes_params.hospital_1,
        "num_hospitals2": settings.archetypes_params.hospital_2,
        "num_hospitals3": settings.archetypes_params.hospital_3,
        "num_hospitals4": settings.archetypes_params.hospital_4,
        "num_hospitals5": settings.archetypes_params.hospital_5,
        "demand_growth": settings.archetypes_params.demand_growth,
        "years": settings.project_settings.time_horizon,
        "periods": settings.project_settings.time_resolution,
    }

    # demand calculation (same code as before, kept intact)
    # For school-only clusters, demand_calculation may return zero load
    # School demand will be added afterwards
    try:
        load_total, users = demand_calculation(
            demand_params["lat"],
            demand_params["cooling_period"],
            demand_params["num_h_tier1"],
            demand_params["num_h_tier2"],
            demand_params["num_h_tier3"],
            demand_params["num_h_tier4"],
            demand_params["num_h_tier5"],
            0,
            demand_params["num_hospitals1"],
            demand_params["num_hospitals2"],
            demand_params["num_hospitals3"],
            demand_params["num_hospitals4"],
            demand_params["num_hospitals5"],
            demand_params["demand_growth"],
            demand_params["years"],
            demand_params["periods"],
        )
    except ValueError as e:
        if "Total load is zero" in str(e):
            # School-only cluster - create empty load dataframe
            periods = demand_params["periods"]
            years = demand_params["years"]
            load_total = pd.DataFrame(0, index=range(periods), columns=[f'Year_{i+1}' for i in range(years)])
            users = []
        else:
            raise

    # school demand assembly (unchanged)
    school_total_demand = getattr(settings.archetypes_params, "school_total_demand", None)
    school_weights_path = Path('/Users/matteo/Desktop/Tesi/Data sheet/School_weights.csv')
    weights_df = pd.read_csv(school_weights_path)
    weights = pd.to_numeric(weights_df['weights'], errors='coerce').values

    num_years = demand_params["years"]
    periods_per_year = demand_params["periods"]
    demand_growth = demand_params["demand_growth"]

    year = 2025

    # Convert school_total_demand to float with error handling
    try:
        school_demand_value = float(school_total_demand) if school_total_demand not in [None, "", "nan", "NaN"] else 0.0
    except (ValueError, TypeError):
        school_demand_value = 0.0

    if school_demand_value == 0:
        school_demand_profile_year = np.zeros(len(weights))
    else:
        school_demand_profile_year = weights * school_demand_value

    school_demand_full = np.zeros(num_years * periods_per_year)
    for y in range(num_years):
        growth_factor = (1 + demand_growth) ** y
        start = y * periods_per_year
        end = (y + 1) * periods_per_year
        school_demand_full[start:end] = school_demand_profile_year * growth_factor

    if isinstance(load_total, pd.DataFrame):
        school_demand_matrix = school_demand_full.reshape((num_years, periods_per_year)).T
        school_demand_df = pd.DataFrame(
            school_demand_matrix,
            index=load_total.index,
            columns=load_total.columns,
        )

        load_total_with_school = load_total + school_demand_df
        demand_df = load_total_with_school.copy()
    elif isinstance(load_total, pd.Series):
        load_total = load_total.add(school_demand_full[:len(load_total)], fill_value=0)
        demand_df = load_total.to_frame(name=str(year))
    else:
        raise ValueError("load_total wrong type")
    
    # Validate that total demand is not zero after adding school demand
    if demand_df.sum().sum() == 0:
        raise ValueError("Error: Total load is zero. Please input at least one non-zero load (household, hospital, or school).")

    demand_df.insert(0, "Periods", range(1, len(demand_df) + 1))
    demand_df.to_csv(pm.AGGREGATED_DEMAND_FILE_PATH, index=False)

    # PVGIS download and resource file creation
    pv_params = settings.resource_assessment
    try:
        pv_data = download_pvgis_pv_data(
            res_name="Solar PV",
            base_URL=settings.pvgis_params.pvgis_base_url,
            output_format=settings.pvgis_params.pvgis_output_format,
            lat=pv_params.lat,
            lon=pv_params.lon,
            nom_power=pv_params.nom_power,
            tilt=pv_params.tilt,
            azimuth=pv_params.azim,
            ro_ground=pv_params.ro_ground,
            k_T=pv_params.k_T,
            NMOT=pv_params.NMOT,
            T_NMOT=pv_params.T_NMOT,
            G_NMOT=pv_params.G_NMOT,
        )
    except Exception as e:
        # Log the failure and fall back to a zero PV series so the optimization can continue
        import traceback
        print(f"PVGIS download/processing error for lat={pv_params.lat} lon={pv_params.lon}: {e}")
        traceback.print_exc()
        # create a zero time series with the same number of periods as the demand
        try:
            periods_total = num_years * periods_per_year
        except Exception:
            # fallback to 1 day of 24 hours if those values are not available
            periods_total = 24
        dataf = pd.DataFrame({
            'Periods': list(range(1, periods_total + 1)),
            'Solar PV': [0.0] * periods_total,
        })
        dataf.set_index('Periods', inplace=True)
        pv_data = dataf

    if isinstance(pv_data, pd.Series):
        pv_data = pv_data.to_frame(name='Solar PV')
    elif isinstance(pv_data, pd.DataFrame):
        if pv_data.shape[1] == 1:
            pv_data.columns = ['Solar PV']

    pv_data.insert(0, 'Periods', range(1, len(pv_data) + 1))
    pv_data.to_csv(pm.RESOURCE_FILE_PATH, index=False)

    # build and solve model - Model class only accepts settings parameter
    # Inject our PathManager instance into the Model after creation
    model = Model(settings)
    model.path_manager = pm  # Override the Model's internal PathManager
    
    # Limit Gurobi threads for better parallelization across multiple clusters
    # With 4 clusters running in parallel × 2 threads each = 8 threads total (optimal for 8-core CPU)
    import os
    os.environ['GRB_LICENSE_FILE'] = '/Users/matteo/Desktop/POLIMI/gurobi.lic'
    
    solver = "gurobi"
    problem_fn = project_folder / "model.lp"
    log_path = project_folder / "solver.log"
    
    # Solve the model using default Gurobi settings
    solution = model.solve_single_objective(solver=solver, problem_fn=str(problem_fn), log_path=str(log_path))
    model.solution = solution

    results_folder = project_folder / "results"
    results_folder.mkdir(exist_ok=True)

    try:
        optimization_goal = "NPC"
        cost_details = get_cost_details(model, optimization_goal)
        lcoe = calculate_lcoe(model, optimization_goal)

        capital_cost = cost_details.get("Total Investment Cost (Actualized)")
        o_and_m_cost = cost_details.get("Total Fixed O&M Cost (Actualized)")
        battery_replacement_cost = cost_details.get("Total Battery Replacement Cost (Actualized)")
        salvage_value = cost_details.get("Total Salvage Value (Actualized)")

        costs_df = pd.DataFrame([{"capital_cost": capital_cost,
                                  "o_and_m_cost": o_and_m_cost,
                                  "battery_replacement_cost": battery_replacement_cost,
                                  "lcoe": lcoe,
                                  "salvage_value": salvage_value}])
        costs_df.to_csv(results_folder / "costs.csv", index=False)
    except Exception as e:
        print(f"Error exporting costs: {e}")

    # energy & postprocessing (same logic kept)
    try:
        energy_usage = calculate_energy_usage(model)

        total_demand = model.parameters['DEMAND'].sum().values.item() / 1e6
        total_production = model.get_solution_variable('Energy Production by Renewables').sum().values.item() / 1e6
        total_curtailment = model.get_solution_variable('Curtailment by Renewables').sum().values.item() / 1e6
        battery_inflow = model.get_solution_variable('Battery Inflow') if model.has_battery else None
        battery_outflow = model.get_solution_variable('Battery Outflow') if model.has_battery else None

        total_battery_inflow = battery_inflow.sum().values.item() / 1e6 if battery_inflow is not None else 0
        total_battery_outflow = battery_outflow.sum().values.item() / 1e6 if battery_outflow is not None else 0
        battery_losses_calculated = total_battery_inflow - total_battery_outflow

        # Comprehensive loss tracking from model variables
        total_losses = 0
        conversion_losses_res = 0
        conversion_losses_battery = 0
        conversion_losses_generator = 0
        feed_in_losses_dc = 0
        charge_losses_dc = 0
        
        # Conversion losses - Renewable Sources
        conversion_losses_var = model.get_solution_variable('Conversion Losses - Renewable Sources')
        if conversion_losses_var is not None:
            conversion_losses_res = conversion_losses_var.sum().values.item() / 1e6
            total_losses += conversion_losses_res
        
        # Battery losses - check if RES connected to battery (DC system) or direct battery losses
        if model.has_battery:
            # Check if RES connected to battery (DC system architecture)
            res_connected_to_battery = any(
                model.parameters['RES_CONNECTED_TO_BATTERY'].sel(renewable_sources=res).item() 
                for res in model.sets.renewable_sources.values
            ) if 'RES_CONNECTED_TO_BATTERY' in model.parameters else False
            
            if res_connected_to_battery:
                # DC System losses: Feed In + Charge losses
                try:
                    feed_in = model.get_solution_variable('Feed In Losses - DC System')
                    charge = model.get_solution_variable('Charge Losses - DC System')
                    if feed_in is not None:
                        feed_in_losses_dc = feed_in.sum().values.item() / 1e6
                        total_losses += feed_in_losses_dc
                    if charge is not None:
                        charge_losses_dc = charge.sum().values.item() / 1e6
                        total_losses += charge_losses_dc
                except (ValueError, KeyError):
                    pass
            else:
                # Direct battery conversion losses
                try:
                    battery_losses_var = model.get_solution_variable('Conversion Losses - Battery')
                    if battery_losses_var is not None:
                        conversion_losses_battery = battery_losses_var.sum().values.item() / 1e6
                        total_losses += conversion_losses_battery
                except (ValueError, KeyError):
                    # Fallback: use calculated battery losses if explicit variable not available
                    conversion_losses_battery = battery_losses_calculated
                    total_losses += conversion_losses_battery
        
        # Conversion losses - Generator
        if model.has_generator:
            try:
                gen_losses_var = model.get_solution_variable('Conversion Losses - Generator')
                if gen_losses_var is not None:
                    conversion_losses_generator = gen_losses_var.sum().values.item() / 1e6
                    total_losses += conversion_losses_generator
            except (ValueError, KeyError):
                pass

        energy_usage.update({
            "Total Demand (MWh)": total_demand,
            "Total RES Production (MWh)": total_production,
            "Total Curtailment (MWh)": total_curtailment,
            "Total Battery Inflow (MWh)": total_battery_inflow,
            "Total Battery Outflow (MWh)": total_battery_outflow,
            "Battery Losses Calculated (MWh)": battery_losses_calculated,
            "Conversion Losses - RES (MWh)": conversion_losses_res,
            "Conversion Losses - Battery (MWh)": conversion_losses_battery,
            "Feed In Losses - DC System (MWh)": feed_in_losses_dc,
            "Charge Losses - DC System (MWh)": charge_losses_dc,
            "Conversion Losses - Generator (MWh)": conversion_losses_generator,
            "Total Losses (MWh)": total_losses,
        })

        energy_df = pd.DataFrame([energy_usage])
        energy_df.to_csv(results_folder / "energy.csv", index=False)
    except Exception as e:
        print(f"error calculating energy usage: {e}")

    if solution is not None:
        # Custom sizing calculation WITHOUT rounding to preserve small values
        categories = []
        data_rows = []
        
        # Solar PV
        res_units = model.get_solution_variable('Unit of Nominal Capacity for Renewables')
        if res_units is not None:
            res_nominal_capacity = model.parameters['RES_NOMINAL_CAPACITY']
            for source in res_units.renewable_sources.values:
                capacity = (res_units.sel(renewable_sources=source).values * 
                           res_nominal_capacity.sel(renewable_sources=source).values) / 1000
                row = [f"{source} (kW)", 0.0]
                row.extend([float(cap) for cap in capacity])
                row.append(float(capacity[-1]))
                data_rows.append(row)
        
        # Battery
        if model.has_battery:
            bat_units = model.get_solution_variable('Unit of Nominal Capacity for Batteries')
            if bat_units is not None:
                battery_nominal_capacity = model.parameters['BATTERY_NOMINAL_CAPACITY']
                capacity = (bat_units.values * battery_nominal_capacity.values) / 1000
                row = ["Battery (kWh)", 0.0]
                row.extend([float(cap) for cap in capacity])
                row.append(float(capacity[-1]))
                data_rows.append(row)
        
        # Create DataFrame with proper precision
        if data_rows:
            num_steps = len(data_rows[0]) - 3  # excluding Component, Existing, Total
            columns = ['Component', 'Existing'] + [f'Step {i+1}' for i in range(num_steps)] + ['Total']
            sizing_df = pd.DataFrame(data_rows, columns=columns)
            sizing_df.to_csv(results_folder / "sizing_results.csv", index=False, float_format='%.6f')
        
        conversion_sizing_df = get_conversion_sizing_results(model)
        # Convert numeric columns to float to preserve decimal values
        for col in conversion_sizing_df.columns:
            if col != 'Component':
                conversion_sizing_df[col] = pd.to_numeric(conversion_sizing_df[col], errors='coerce').astype(float)
        conversion_sizing_df.to_csv(results_folder / "conversion_sizing_results.csv", index=False, float_format='%.6f')
        
        battery_soc_df = get_battery_soc(model)
        battery_soc_df.to_csv(results_folder / "battery_soc.csv", index=False)
        renewables_usage_df = get_renewables_usage(model)
        renewables_usage_df.to_csv(results_folder / "renewables_usage.csv", index=False)
    else:
        print("opt failed.")

    # demand export (kept identical)
    raw_demands_folder = pm.DEMAND_FOLDER_PATH / "raw_user_demands"
    os.makedirs(raw_demands_folder, exist_ok=True)

    raw_data = {}
    for user in users:
        data = np.array(user.demand_data)
        if data.ndim == 2:
            if data.shape[0] == periods_per_year and data.shape[1] == num_years:
                data = data.T
            data = data.reshape(num_years * periods_per_year)
        elif data.ndim == 1:
            pass
        else:
            data = np.full(num_years * periods_per_year, data)
        raw_data[user.name] = data

    raw_data["School"] = school_demand_full

    periods = []
    years_list = []
    for y in range(num_years):
        for p in range(periods_per_year):
            periods.append(p + 1)
            years_list.append(year + y)

    raw_df = pd.DataFrame(raw_data)
    raw_df.insert(0, "Period", periods)
    raw_df.insert(1, "Year", years_list)
    raw_df.to_csv(raw_demands_folder / "demand_breakdown_raw.csv", index=False)

    agg_rows = []
    for y in range(num_years):
        start_idx = y * periods_per_year
        end_idx = (y + 1) * periods_per_year
        row = {"Year": year + y}
        for col in raw_data:
            row[col] = raw_df.loc[start_idx:end_idx-1, col].sum()
        agg_rows.append(row)

    total_row = {"Year": "Total"}
    for col in raw_data:
        total_row[col] = raw_df[col].sum()
    agg_rows.append(total_row)

    agg_df = pd.DataFrame(agg_rows)
    agg_df.to_csv(raw_demands_folder / "demand_breakdown_aggregated.csv", index=False)


# CLI compatibility
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--yaml", type=str, required=True)
    args = parser.parse_args()
    run_yaml(args.yaml)
