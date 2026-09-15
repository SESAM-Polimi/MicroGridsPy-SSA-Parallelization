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
H_VMEM="${H_VMEM:-5G}"      # Aug 2026 run peaked at 2.8 GB (maxvmem, full export)
H_RT="${H_RT:-06:00:00}"
MAX_CONCURRENT="${MAX_CONCURRENT:-16}"

# --- model / pipeline options ---
SOLVER="${SOLVER:-gurobi}"
# One core per SGE slot: without this Gurobi may start many threads per task and
# overload the node (SGE "alarm" state). Time limit < H_RT, so a slow cluster ends
# with a recorded status (error.txt) instead of being killed silently by SGE.
SOLVER_THREADS="${SOLVER_THREADS:-1}"
SOLVER_TIME_LIMIT="${SOLVER_TIME_LIMIT:-18000}"   # seconds (5 h; H_RT is 6 h)
HORIZON="${HORIZON:-20}"
# System extensions (OPTIONAL). PV+battery only is feasible by default (the thesis
# design), so leave this EMPTY for a faithful reproduction. Set it only for
# sensitivity studies:  "--include-generator"  or  "--max-lost-load 0.05 --lost-load-cost 5.0"
FEASIBILITY_FLAGS="${FEASIBILITY_FLAGS:-}"
# RUN_MODE: "full" = prepare(PVGIS)+solve in each task (needs internet on nodes);
#           "solve-only" = solve pre-staged inputs (run hpc prefetch first, offline nodes).
RUN_MODE="${RUN_MODE:-full}"
# Results export policy (flash-optimised). "core" writes only summary.json + dispatch.<fmt>
# (everything else is rebuilt offline by mgpy2.reporting); "full" writes the legacy bundle.
EXPORT_PROFILE="${EXPORT_PROFILE:-core}"
DISPATCH_FORMAT="${DISPATCH_FORMAT:-parquet}"   # parquet (small/fast) or csv

activate_env() {
  eval "$("$CONDA_BASE/bin/conda" shell.bash hook)"
  conda activate "$CONDA_ENV"
  export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
  export PYTHONUNBUFFERED=1   # log lines appear immediately and survive a crash
}

# Before a task writes anything: can we still write to scratch? If not (quota full),
# record it in $HOME (separate quota), HOLD the rest of the array so it does not burn
# through thousands of tasks, and stop. Resume later with:  qrls <JOB_ID>
check_disk_or_hold() {
  local dir="$1" probe="$1/.space_probe.${JOB_ID:-0}.${SGE_TASK_ID:-0}"
  if dd if=/dev/zero of="$probe" bs=1M count=10 status=none 2>/dev/null; then
    rm -f "$probe"
    return 0
  fi
  rm -f "$probe"
  echo "[$(date)] job ${JOB_ID:-?} task ${SGE_TASK_ID:-?}: cannot write 10 MB in $dir" \
       "(quota full?). Array put on hold." >> "$HOME/mgpy_disk_alert.log"
  [ -n "${JOB_ID:-}" ] && { qhold "$JOB_ID" >/dev/null 2>&1 || true; }
  exit 3
}

mode_flag() { [ "$RUN_MODE" = "solve-only" ] && echo "--solve-only" || echo ""; }
