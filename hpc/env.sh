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
SGE_NODES="${SGE_NODES:-node-1-3}"  # "" = no restriction; two nodes: "node-1-2|node-1-3"
H_VMEM="${H_VMEM:-4G}"      # per slot. Benchmark 17 Sep 2026: peak 2.3 GB (barrier+crossover, core export)
H_RT="${H_RT:-06:00:00}"
MAX_CONCURRENT="${MAX_CONCURRENT:-16}"   # 16 cores per node: use 32 with two nodes

# --- model / pipeline options ---
SOLVER="${SOLVER:-gurobi}"
# One core per SGE slot: without this Gurobi may start many threads per task and
# overload the node (SGE "alarm" state). Time limit < H_RT, so a slow cluster ends
# with a recorded status (error.txt) instead of being killed silently by SGE.
# Inside an SGE job submitted with "-pe smp N", SGE sets NSLOTS=N: the thread count
# then follows the cores actually reserved (no oversubscription). Default 1.
SOLVER_THREADS="${SOLVER_THREADS:-${NSLOTS:-1}}"
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
DISPATCH_FORMAT="${DISPATCH_FORMAT:-parquet}"   # parquet (small/fast), csv, or none (summary.json only)

# Extra solver-native options, ';'-separated (no commas: qsub -v splits on them).
# Gurobi default: barrier WITHOUT crossover when no dispatch is written (production runs),
# barrier WITH crossover when a dispatch file is exported.
# Why (9 Oct 2026, 52-cluster comparison set, battery ageing on, 1 thread): without
# crossover NPC is within 7e-9 and PV/battery sizes within 6e-7 of the crossover solution,
# and the solve median drops from 605 s to 153 s (worst case 2,879 s -> 574 s). Crossover
# only picks a corner among equally cheap solutions; without it ~0.05-0.2 % of battery
# discharge falls in hours that also charge (PV surplus burnt as losses instead of
# curtailed, same cost). That is invisible in summary.json but shows in a dispatch file,
# hence crossover stays on whenever DISPATCH_FORMAT is not "none". (The 17 Sep 2026 rule
# "never Crossover=0" was written for runs that exported dispatch.)
# Note "${VAR-default}" (no colon): an explicitly EMPTY value (SOLVER_OPTS=) is kept,
# so benchmarks can still request pure solver defaults.
if [ "$SOLVER" = "gurobi" ]; then
  if [ "$DISPATCH_FORMAT" = "none" ]; then _gurobi_default="Method=2;Crossover=0"; else _gurobi_default="Method=2"; fi
  SOLVER_OPTS="${SOLVER_OPTS-$_gurobi_default}"
else
  SOLVER_OPTS="${SOLVER_OPTS-}"
fi

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

# "Method=2;Crossover=0" -> OPT_FLAGS=(--solver-opt Method=2 --solver-opt Crossover=0)
# Fills the global array OPT_FLAGS (bash functions cannot return arrays).
build_opt_flags() {
  OPT_FLAGS=()
  local _opts kv
  IFS=';' read -r -a _opts <<< "$SOLVER_OPTS"
  for kv in ${_opts[@]+"${_opts[@]}"}; do
    if [[ -n "$kv" ]]; then OPT_FLAGS+=(--solver-opt "$kv"); fi
  done
}

mode_flag() { [ "$RUN_MODE" = "solve-only" ] && echo "--solve-only" || echo ""; }
