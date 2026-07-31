# ---------- updated_orchestrator.py (replace your original orchestrator script) ----------

import sys
import os
import pandas as pd
import yaml
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
import sys
from pathlib import Path
import os
import pandas as pd
import yaml
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
import time

# ensure the directory that contains run_nostreamlit.py is importable by worker processes
RUN_SCRIPT = Path("/Users/matteo/Desktop/Tesi/MGPY/run_nostreamlit_update.py")
RUN_SCRIPT_DIR = str(RUN_SCRIPT.resolve().parent)
if RUN_SCRIPT_DIR not in sys.path:
    sys.path.insert(0, RUN_SCRIPT_DIR)

# local project root (your original)
sys.path.insert(0, "/Users/matteo/Desktop/MicroGridsPy-Development_Linopy-2")

# import the function that actually runs the optimization in-process
# (this requires the second file below to expose `run_yaml` and keep CLI compatibility)
try:
    import run_nostreamlit_update as run_nostreamlit  # noqa: E402
except Exception:
    # if import fails here, the child worker initializer will also attempt import
    run_nostreamlit = None

# Paths
SAMPLE_CSV = Path("/Users/matteo/Desktop/TEST_supercomputer/hpc_country/BEN/advanced_sample.csv")
YAML_TEMPLATE = Path('/Users/matteo/Desktop/Tesi/Data sheet/a.yaml')
PROJECTS_DIR = Path('/Volumes/2TB/outputs_BEN_advanced')


# template inputs (static)
TEMPLATE_INPUTS_DIR = Path("/Users/matteo/Desktop/MicroGridsPy-Development_Linopy-2/microgridspy/inputs")
STATIC_CSVS = [
    "Battery Cost.csv",
    "Fuel Specific Cost.csv",
    "Grid Availability.csv",
    "RES Cost.csv",
    "Temperature.csv",
]

# worker initializer: run once per child process
def worker_init():
    global run_module
    # make sure the run script dir is on sys.path in the worker
    if str(RUN_SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(RUN_SCRIPT_DIR))
    try:
        import run_nostreamlit_update as run_module  # noqa: F401
    except Exception as e:
        # store None and let process_row raise a clearer error
        run_module = None

def process_row(row):
    """Worker function. Expects `row` as a plain dict (or simple tuple if you change).
    This version avoids subprocess.spawn by calling run_nostreamlit.run_yaml() directly.
    """
    try:
        cat = str(row['cat'])
        project_dir = PROJECTS_DIR / cat
        
        # Skip if results already exist
        results_dir = project_dir / "results"
        costs_file = results_dir / "costs.csv"
        if costs_file.exists():
            print(f"[{cat}][SKIP] Results already exist")
            return
        
        project_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = project_dir / f"{cat}.yaml"

        # input folder
        inputs_dir = project_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        # copy static csvs only if missing (avoid re-copying and extra IO)
        for fname in STATIC_CSVS:
            src = TEMPLATE_INPUTS_DIR / fname
            dst = inputs_dir / fname
            if src.exists() and not dst.exists():
                try:
                    shutil.copy(src, dst)
                except Exception:
                    # best-effort copy; continue even if one fails
                    pass

        # load yaml template and update
        with open(YAML_TEMPLATE) as f:
            yaml_data = yaml.safe_load(f)

        # Update archetypes and coordinates in memory
        for col in yaml_data.get('archetypes_params', {}):
            if col in row:
                val = row[col]
                if hasattr(val, "item"):
                    val = val.item()
                yaml_data['archetypes_params'][col] = val
        if 'archetypes_params' in yaml_data:
            # school parameter update
            if 'school_total_demand' in row and pd.notna(row['school_total_demand']) and row['school_total_demand'] != "":
                val = row['school_total_demand']
                if hasattr(val, "item"):
                    val = val.item()
                yaml_data['archetypes_params']['school_total_demand'] = val
            elif 'school_total_demand' not in yaml_data['archetypes_params']:
                yaml_data['archetypes_params']['school_total_demand'] = 0

        # cooling parameter update
        if 'cooling' in row and 'archetypes_params' in yaml_data:
            cooling_val = row['cooling']
            if pd.notna(cooling_val) and cooling_val != "":
                yaml_data['archetypes_params']['cooling_period'] = str(cooling_val)

        # coordinates update
        for coord in ['lat', 'lon']:
            if coord in row and 'resource_assessment' in yaml_data:
                val = row[coord]
                if hasattr(val, "item"):
                    val = val.item()
                yaml_data['resource_assessment'][coord] = val

        # update battery cost CSV for multi-step investments
        battery_cost_path = inputs_dir / "Battery Cost.csv"
        if battery_cost_path.exists() and 'battery_params' in yaml_data:
            battery_cost = yaml_data['battery_params'].get('battery_specific_investment_cost', 0.65)
            num_steps = yaml_data.get('advanced_settings', {}).get('num_steps', 1)
            with open(battery_cost_path, "r") as f:
                lines = f.readlines()
            header = lines[0] if lines else "Step,Cost\n"
            new_lines = [header]
            for i in range(num_steps):
                new_lines.append(f"{i+1},{battery_cost}\n")
            with open(battery_cost_path, "w") as f:
                f.writelines(new_lines)

        # update RES cost CSV for multi-step investments
        res_cost_path = inputs_dir / "RES Cost.csv"
        if res_cost_path.exists() and 'renewables_params' in yaml_data:
            res_costs = yaml_data['renewables_params'].get('res_specific_investment_cost', [0.95])
            num_steps = yaml_data.get('advanced_settings', {}).get('num_steps', 1)
            with open(res_cost_path, "r") as f:
                lines = f.readlines()
            header = lines[0] if lines else "Step,Cost\n"
            new_lines = [header]
            for i in range(num_steps):
                cost = res_costs[i] if i < len(res_costs) else res_costs[-1]
                new_lines.append(f"{i+1},{cost}\n")
            with open(res_cost_path, "w") as f:
                f.writelines(new_lines)

        # final yaml
        with open(yaml_path, "w") as f:
            yaml.dump(yaml_data, f, sort_keys=False)

        # RUN in-process using the imported run module (no subprocess spawn)
        if run_nostreamlit is None:
            # try import if not available (this happens in the parent before fork)
            import importlib
            run_mod = importlib.import_module('run_nostreamlit_update')
        else:
            run_mod = run_nostreamlit

        if not hasattr(run_mod, 'run_yaml'):
            raise RuntimeError('run_nostreamlit module does not expose run_yaml(yaml_path)')

        # call the function that runs the optimization using the yaml path
        try:
            run_mod.run_yaml(str(yaml_path))
            print(f"[{row['cat']}][OK] finished")
        except Exception as e:
            print(f"[{row['cat']}][ERROR] {e}")
            # write an error marker so user can inspect this project's folder, then continue
            try:
                results_dir = project_dir / "results"
                results_dir.mkdir(parents=True, exist_ok=True)
                with open(results_dir / "error.txt", "w") as ef:
                    ef.write(str(e))
            except Exception:
                pass
            return

    except Exception as e:
        print(f"[DEBUG] Exception in process_row for cat {row.get('cat', 'unknown')}: {e}")
        import traceback
        traceback.print_exc()
        raise


# main
def main():
    start = time.time()
    df = pd.read_csv(SAMPLE_CSV)
    df = df[~df.iloc[:, 0].astype(str).str.startswith("//")]

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    # copy sample.csv to outputs (single copy)
    sample_dst = PROJECTS_DIR / "sample.csv"
    if not sample_dst.exists():
        shutil.copy(SAMPLE_CSV, sample_dst)

    # prepare rows as simple dicts (ok for heavy tasks) or tuples for minimal pickle
    rows = df.to_dict('records')



    # Robust CPU detection and worker allocation
    cpu_count = os.cpu_count() or 1
    num_workers = 3

    print(f"Starting with {num_workers} workers (cpu_count={cpu_count})")

    with ProcessPoolExecutor(max_workers=num_workers, initializer=worker_init) as executor:
        futures = [executor.submit(process_row, row) for row in rows]
        for fut in as_completed(futures):
            # will re-raise exceptions here
            fut.result()

    end = time.time()
    print(f"Total parallel execution time: {end - start:.2f} seconds")


if __name__ == '__main__':
    main()


