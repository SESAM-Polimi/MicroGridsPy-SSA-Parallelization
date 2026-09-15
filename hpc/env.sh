#!/bin/bash
# =====================================================================
# hpc/env.sh — SINGLE place to configure the HPC run. EDIT THIS FILE.
# Sourced by submit_jobs.sh (resources -> qsub CLI) and by the array
# tasks (conda activation + runtime flags). Every value can also be
# overridden from the environment, e.g.  H_VMEM=12G ./hpc/submit_jobs.sh
# =====================================================================

# --- your conda install + env (the ONLY hardcoded path you must set) ---
CONDA_BASE="${CONDA_BASE:-$HOME/miniconda3}"     # conda base; $HOME works on every node
CONDA_ENV="${CONDA_ENV:-mgpy_clean}"          # env that imports BOTH engines

# --- which sample (country) to run: the ONLY place to change it ---
# Path is relative to the repo root (all hpc scripts cd there first).
SAMPLE_CSV="${SAMPLE_CSV:-data/sample_input_2025/ETH/advanced_sample.csv}"
export SAMPLE_CSV

# --- SGE resources (applied via qsub CLI, so they are never ignored) ---
SGE_QUEUE="${SGE_QUEUE:-energia.q}"
SGE_NODES="${SGE_NODES:-node-1-3}"  # or "" for no restriction
H_VMEM="${H_VMEM:-8G}"                            # 20-yr hourly model needs headroom
H_RT="${H_RT:-06:00:00}"
MAX_CONCURRENT="${MAX_CONCURRENT:-16}"

# --- model / pipeline options ---
SOLVER="${SOLVER:-gurobi}" 
HORIZON="${HORIZON:-20}"
# System extensions (OPTIONAL). PV+battery only is feasible by default (the thesis
# design), so leave this EMPTY for a faithful reproduction. Set it only for
# sensitivity studies:  "--include-generator"  or  "--max-lost-load 0.05 --lost-load-cost 5.0"
FEASIBILITY_FLAGS="${FEASIBILITY_FLAGS:-}"
# RUN_MODE: "full" = prepare(PVGIS)+solve in each task (needs internet on nodes);
#           "solve-only" = solve pre-staged inputs (run hpc prefetch first, offline nodes).
RUN_MODE="${RUN_MODE:-full}"

activate_env() {
  eval "$("$CONDA_BASE/bin/conda" shell.bash hook)"
  conda activate "$CONDA_ENV"
  export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
}

mode_flag() { [ "$RUN_MODE" = "solve-only" ] && echo "--solve-only" || echo ""; }
