#!/bin/bash
# =====================================================================
# hpc/benchmark_threads.sh — how many threads per solve? (run on the LOGIN node)
#
# Submits every (config x task) pair as its own 1-task job on ONE node, each
# config writing to its own projects folder so runs never collide.
#
#   ./hpc/benchmark_threads.sh                 # default tasks below
#   ./hpc/benchmark_threads.sh 12426 6781      # your own task ids
#
# Compare afterwards with:  qacct -j bench_T4   (wallclock, maxvmem, cpu)
# and the "Solved in ... seconds" line of each gurobi_solve.log.
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."            # repo root, whatever folder we start from
source hpc/env.sh

# p10 / p50 / p90 / p100 of the Aug-2026 solve times (ETH, ok rows)
DEFAULT_TASKS=(12426 6781 5028 8179)
TASKS=("$@")
(( ${#TASKS[@]} )) || TASKS=("${DEFAULT_TASKS[@]}")

BENCH_NODE="${BENCH_NODE:-node-1-3}"
BENCH_ROOT="${BENCH_ROOT:-$PWD/bench/$(date +%Y%m%d_%H%M)}"

# name | slots (= Gurobi threads via NSLOTS) | h_vmem PER SLOT | solver options
CONFIGS=(
  "T1|1|6G|"
  "T4|4|4G|"
  "T8|8|2G|"
  "T4bar|4|4G|Method=2;Crossover=0"
)

echo "Node: $BENCH_NODE   Tasks: ${TASKS[*]}   Output: $BENCH_ROOT"
for cfg in "${CONFIGS[@]}"; do
  IFS='|' read -r name slots vmem opts <<< "$cfg"
  mkdir -p "$BENCH_ROOT/$name/projects"     # engine needs the folder to be named 'projects'
  for t in "${TASKS[@]}"; do
    qsub -N "bench_${name}" -t "$t" \
         -q "$SGE_QUEUE" -l hostname="$BENCH_NODE" \
         -pe smp "$slots" -l h_vmem="$vmem" -l h_rt="$H_RT" \
         -v "SAMPLE_CSV=$SAMPLE_CSV,MGPY2_PROJECTS_DIR=$BENCH_ROOT/$name/projects,SOLVER_OPTS=$opts" \
         hpc/submit_array.sh
  done
done
echo "Submitted $(( ${#CONFIGS[@]} * ${#TASKS[@]} )) jobs. Watch with: qstat -u $USER"
