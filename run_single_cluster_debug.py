#!/usr/bin/env python3
"""
Debug version of run_single_cluster.py - shows full error output
"""

import sys
import argparse
import pandas as pd
from pathlib import Path
import subprocess
import os

PROJECT_ROOT = Path(__file__).resolve().parent
MGPY_DIR = PROJECT_ROOT / "MGPY"
sys.path.insert(0, str(MGPY_DIR))
MICROGRIDSPY_ROOT = PROJECT_ROOT / "MicroGridsPy-Development_Linopy-2"
sys.path.insert(0, str(MICROGRIDSPY_ROOT))

import yaml
import shutil

SAMPLE_CSV = PROJECT_ROOT / "advanced_sample.csv"
YAML_TEMPLATE = PROJECT_ROOT / "Data_sheet" / "a.yaml"
PROJECTS_DIR = PROJECT_ROOT / "projects"
RUN_SCRIPT = PROJECT_ROOT / "MGPY" / "run_nostreamlit_update.py"

TEMPLATE_INPUTS_DIR = PROJECT_ROOT / "MicroGridsPy-Development_Linopy-2" / "microgridspy" / "inputs"
STATIC_CSVS = [
    "Battery Cost.csv",
    "Fuel Specific Cost.csv",
    "Grid Availability.csv",
    "RES Cost.csv",
    "Temperature.csv",
]


def prepare_cluster_yaml(row):
    """Prepare YAML and input files for a single cluster."""
    cat = str(row['cat'])
    project_dir = PROJECTS_DIR / cat
    
    results_dir = project_dir / "results"
    costs_file = results_dir / "costs.csv"
    if costs_file.exists():
        print(f"[{cat}][SKIP] Results already exist")
        return None
    
    project_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = project_dir / f"{cat}.yaml"

    inputs_dir = project_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    for fname in STATIC_CSVS:
        src = TEMPLATE_INPUTS_DIR / fname
        dst = inputs_dir / fname
        if src.exists() and not dst.exists():
            try:
                shutil.copy(src, dst)
            except Exception:
                pass

    with open(YAML_TEMPLATE) as f:
        yaml_data = yaml.safe_load(f)

    for col in yaml_data.get('archetypes_params', {}):
        if col in row:
            val = row[col]
            if hasattr(val, "item"):
                val = val.item()
            yaml_data['archetypes_params'][col] = val
    
    if 'archetypes_params' in yaml_data:
        if 'school_total_demand' in row and pd.notna(row['school_total_demand']) and row['school_total_demand'] != "":
            val = row['school_total_demand']
            if hasattr(val, "item"):
                val = val.item()
            yaml_data['archetypes_params']['school_total_demand'] = val
        elif 'school_total_demand' not in yaml_data['archetypes_params']:
            yaml_data['archetypes_params']['school_total_demand'] = 0

    if 'cooling' in row and 'archetypes_params' in yaml_data:
        cooling_val = row['cooling']
        if pd.notna(cooling_val) and cooling_val != "":
            yaml_data['archetypes_params']['cooling_period'] = str(cooling_val)

    for coord in ['lat', 'lon']:
        if coord in row and 'resource_assessment' in yaml_data:
            val = row[coord]
            if hasattr(val, "item"):
                val = val.item()
            yaml_data['resource_assessment'][coord] = val

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

    with open(yaml_path, "w") as f:
        yaml.dump(yaml_data, f, sort_keys=False)

    return str(yaml_path), cat


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", type=int, required=True)
    args = parser.parse_args()
    
    df = pd.read_csv(SAMPLE_CSV)
    df = df[~df.iloc[:, 0].astype(str).str.startswith("//")]
    
    row_idx = args.task_id - 1
    if row_idx < 0 or row_idx >= len(df):
        print(f"ERROR: Task ID {args.task_id} out of range (1-{len(df)})")
        sys.exit(1)
    
    row = df.iloc[row_idx].to_dict()
    cat = str(row['cat'])
    
    print(f"========================================")
    print(f"DEBUG MODE - Task ID: {args.task_id}")
    print(f"Cluster: {cat}")
    print(f"========================================")
    
    result = prepare_cluster_yaml(row)
    if result is None:
        print(f"[{cat}] Already completed or skipped")
        sys.exit(0)
    
    yaml_path, _ = result
    print(f"[{cat}] YAML prepared: {yaml_path}")
    print(f"[{cat}] Calling: {RUN_SCRIPT} --yaml {yaml_path}")
    
    cmd = [sys.executable, str(RUN_SCRIPT), "--yaml", yaml_path]
    env = os.environ.copy()
    env['GRB_THREADS'] = '1'
    
    print(f"[{cat}] Starting subprocess (output NOT captured)...")
    print(f"[{cat}] ========================================")
    
    # Run WITHOUT capture so we see all output
    result = subprocess.run(cmd, env=env, timeout=3600)
    
    print(f"[{cat}] ========================================")
    print(f"[{cat}] Subprocess exited with code: {result.returncode}")
    
    if result.returncode == 0:
        print(f"[{cat}] ✓ SUCCESS")
    else:
        print(f"[{cat}] ✗ FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
