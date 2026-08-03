#!/bin/bash
# =====================================================================
# Submit the full country as an SGE job array. Run from the REPO ROOT:
#     ./hpc/submit_jobs.sh
# Reads config from hpc/env.sh (override via env vars, e.g. H_VMEM=12G ...).
# =====================================================================
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
source hpc/env.sh

CSV_FILE="advanced_sample.csv"
[ -f "$CSV_FILE" ] || { echo "ERROR: $CSV_FILE not found in $REPO_ROOT"; exit 1; }

NUM_ROWS=$(grep -v '^//' "$CSV_FILE" | tail -n +2 | wc -l | tr -d ' ')
[ "$NUM_ROWS" -ge 1 ] || { echo "ERROR: no valid rows in $CSV_FILE"; exit 1; }

# Build SGE resource args from env.sh (applied on the CLI => always honoured).
RES=(-q "$SGE_QUEUE" -l "h_vmem=$H_VMEM" -l "h_rt=$H_RT")
[ -n "$SGE_NODES" ] && RES+=(-l "hostname=$SGE_NODES")

echo "=========================================="
echo "MicroGridsPy country run (NEW engine)"
echo "Clusters:    $NUM_ROWS   (array 1-$NUM_ROWS, max $MAX_CONCURRENT concurrent)"
echo "Solver:      $SOLVER | horizon $HORIZON | mode $RUN_MODE"
echo "Feasibility: $FEASIBILITY_FLAGS"
echo "Resources:   ${RES[*]}"
echo "Env:         $CONDA_ENV @ $CONDA_BASE"
echo "Marker:      results/reporting_summary.csv"
echo "=========================================="
read -p "Submit? (y/n) " -n 1 -r; echo ""
[[ $REPLY =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 0; }

mkdir -p logs
qsub "${RES[@]}" -t 1-"$NUM_ROWS" -tc "$MAX_CONCURRENT" hpc/submit_array.sh
echo "Submitted. Monitor with: ./hpc/monitor_jobs.sh"
