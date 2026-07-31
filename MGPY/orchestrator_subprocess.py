# ---------- orchestrator_subprocess.py: Parallelism with process isolation ----------
"""
Processes multiple YAML optimizations in parallel (32 workers) while isolating
each cluster's Gurobi solver in its own subprocess that exits after completion.

This prevents memory accumulation and Gurobi licensing issues by ensuring:
- Each cluster runs in a fresh Python process
- Solver/model objects are completely freed when process exits
- 32 parallel jobs keep runtime fast (~5-15 hours for 1,768 clusters)
- Failed jobs don't affect other workers
"""

import sys
import os
import pandas as pd
import yaml
import shutil
import subprocess
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed


# Define project root dynamically (root of TEST_supercomputer)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Paths
SAMPLE_CSV = PROJECT_ROOT / "advanced_sample.csv"
YAML_TEMPLATE = PROJECT_ROOT / "Data_sheet" / "a.yaml"
PROJECTS_DIR = PROJECT_ROOT / "projects"
RUN_SCRIPT = PROJECT_ROOT / "MGPY" / "run_nostreamlit_update.py"

# template inputs (static)
TEMPLATE_INPUTS_DIR = PROJECT_ROOT / "MicroGridsPy-Development_Linopy-2" / "microgridspy" / "inputs"
STATIC_CSVS = [
    "Battery Cost.csv",
    "Fuel Specific Cost.csv",
    "Grid Availability.csv",
    "RES Cost.csv",
    "Temperature.csv",
]


def prepare_cluster_yaml(row):
    """Prepare YAML and input files for a single cluster (shared by all workers)."""
    cat = str(row['cat'])
    project_dir = PROJECTS_DIR / cat
    
    # Skip if results already exist
    results_dir = project_dir / "results"
    costs_file = results_dir / "costs.csv"
    if costs_file.exists():
        print(f"[{cat}][SKIP] Results already exist")
        return None
    
    project_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = project_dir / f"{cat}.yaml"

    # input folder
    inputs_dir = project_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    # copy static csvs only if missing
    for fname in STATIC_CSVS:
        src = TEMPLATE_INPUTS_DIR / fname
        dst = inputs_dir / fname
        if src.exists() and not dst.exists():
            try:
                shutil.copy(src, dst)
            except Exception:
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

    return str(yaml_path), cat


def run_cluster_subprocess(yaml_path, cat):
    """
    Spawn a subprocess to run a single cluster optimization.
    Subprocess exits completely after solving, freeing all memory.
    This isolates Gurobi and prevents memory accumulation.
    """
    try:
        # Spawn subprocess to run the optimization
        # Each subprocess is fresh: new Python interpreter, new Gurobi session, new model
        cmd = [
            sys.executable,
            str(RUN_SCRIPT),
            "--yaml", yaml_path
        ]
        
        print(f"[{cat}][START] Spawning subprocess for optimization...")
        
        # Set environment for subprocess
        env = os.environ.copy()
        env['GRB_THREADS'] = '1'  # Limit Gurobi to 1 thread per subprocess
        
        # Run subprocess and wait for completion
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=3600  # 1-hour timeout per cluster
        )
        
        if result.returncode == 0:
            print(f"[{cat}][OK] Subprocess completed successfully")
            if result.stdout:
                print(f"[{cat}][STDOUT] {result.stdout[:500]}")
            return True
        else:
            print(f"[{cat}][ERROR] Subprocess failed with return code {result.returncode}")
            print(f"[{cat}][STDERR] {result.stderr[:1000]}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"[{cat}][TIMEOUT] Subprocess exceeded 1-hour limit")
        return False
    except Exception as e:
        print(f"[{cat}][ERROR] Exception spawning subprocess: {e}")
        import traceback
        traceback.print_exc()
        return False


def process_row(row):
    """
    Worker function: prepare YAML, then spawn isolated subprocess for optimization.
    After subprocess exits, worker is ready for next task (memory is freed).
    """
    cat = str(row['cat'])
    try:
        # Prepare YAML and inputs (fast, in-process)
        result = prepare_cluster_yaml(row)
        if result is None:
            return True  # Already completed, skip
        
        yaml_path, cat_name = result
        
        # Run optimization in subprocess (memory isolated, exits after completion)
        success = run_cluster_subprocess(yaml_path, cat_name)
        return success
        
    except Exception as e:
        print(f"[{cat}][ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    start = time.time()
    
    print(f"\n{'='*70}")
    print("MicroGridsPy HPC Orchestrator - Subprocess Isolation Mode")
    print(f"{'='*70}\n")
    
    # Load sample CSV
    df = pd.read_csv(SAMPLE_CSV)
    df = df[~df.iloc[:, 0].astype(str).str.startswith("//")]
    
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    # copy sample.csv to outputs (single copy)
    sample_dst = PROJECTS_DIR / "sample.csv"
    if not sample_dst.exists():
        shutil.copy(SAMPLE_CSV, sample_dst)

    rows = df.to_dict('records')

    # Determine number of parallel workers
    num_workers = int(os.environ.get("NSLOTS", 
                      os.environ.get("SLURM_CPUS_PER_TASK", os.cpu_count() or 1)))

    print(f"Starting orchestrator with {num_workers} parallel workers")
    print(f"Total clusters to process: {len(rows)}")
    print(f"Each worker spawns subprocess-isolated optimizations")
    print(f"Gurobi threads per subprocess: 1 (GRB_THREADS=1)\n")

    completed = 0
    failed = 0
    skipped = 0
    
    # ProcessPoolExecutor: spawn subprocesses for each cluster
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(process_row, row) for row in rows]
        
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                success = fut.result()
                if success is None:
                    skipped += 1
                elif success:
                    completed += 1
                else:
                    failed += 1
                
                # Progress report every 50 clusters
                if (completed + failed) % 50 == 0:
                    print(f"Progress: {completed + failed}/{len(rows)} clusters processed "
                          f"(Completed: {completed}, Failed: {failed}, Skipped: {skipped})")
            except Exception as e:
                failed += 1
                print(f"Task exception: {e}")

    end = time.time()
    elapsed_hours = (end - start) / 3600
    
    print(f"\n{'='*70}")
    print(f"Orchestrator Complete")
    print(f"{'='*70}")
    print(f"Total execution time: {elapsed_hours:.2f} hours")
    print(f"Completed: {completed}/{len(rows)}")
    print(f"Failed: {failed}")
    print(f"Skipped: {skipped}")
    print(f"{'='*70}\n")


if __name__ == '__main__':
    main()
