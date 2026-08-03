#!/bin/bash
# =====================================================================
# hpc/env.sh — SINGLE place to configure the HPC run. EDIT THIS FILE.
# Sourced by submit_jobs.sh (resources -> qsub CLI) and by the array
# tasks (conda activation + runtime flags). Every value can also be
# overridden from the environment, e.g.  H_VMEM=12G ./hpc/submit_jobs.sh
# =====================================================================

# --- your conda install + env (the ONLY hardcoded path you must set) ---
CONDA_BASE="${CONDA_BASE:-$HOME/miniconda3}"     # <-- CHANGE to your conda base
CONDA_ENV="${CONDA_ENV:-mgpy_planning}"          # env that imports BOTH engines

# --- SGE resources (applied via qsub CLI, so they are never ignored) ---
SGE_QUEUE="${SGE_QUEUE:-energia.q}"
SGE_NODES="${SGE_NODES:-node-1-3|node-1-6|node-1-7|node-1-8}"  # or "" for no restriction
H_VMEM="${H_VMEM:-8G}"                            # 20-yr hourly model needs headroom
H_RT="${H_RT:-06:00:00}"
MAX_CONCURRENT="${MAX_CONCURRENT:-64}"

# --- model / pipeline options ---
SOLVER="${SOLVER:-highs}"
HORIZON="${HORIZON:-20}"
# Feasibility (choose ONE — PV+battery only is infeasible in the new engine):
#   "--include-generator"                or
#   "--max-lost-load 0.05 --lost-load-cost 5.0"
FEASIBILITY_FLAGS="${FEASIBILITY_FLAGS:---include-generator}"
# RUN_MODE: "full" = prepare(PVGIS)+solve in each task (needs internet on nodes);
#           "solve-only" = solve pre-staged inputs (run hpc prefetch first, offline nodes).
RUN_MODE="${RUN_MODE:-full}"

activate_env() {
  eval "$("$CONDA_BASE/bin/conda" shell.bash hook)"
  conda activate "$CONDA_ENV"
  export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
}

mode_flag() { [ "$RUN_MODE" = "solve-only" ] && echo "--solve-only" || echo ""; }
