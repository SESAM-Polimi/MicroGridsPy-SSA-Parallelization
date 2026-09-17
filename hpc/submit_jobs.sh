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
activate_env   # python + pandas are needed to count the rows

# Row count comes from the SAME code that maps task id -> row (mgpy2/sample.py),
# so the array size can never disagree with what each task reads.
NUM_ROWS=$(python -m mgpy2.sample "$SAMPLE_CSV") \
  || { echo "ERROR: cannot read sample $SAMPLE_CSV"; exit 1; }
[ "$NUM_ROWS" -ge 1 ] || { echo "ERROR: no valid rows in $SAMPLE_CSV"; exit 1; }

# Build SGE resource args from env.sh (applied on the CLI => always honoured).
RES=(-q "$SGE_QUEUE" -l "h_vmem=$H_VMEM" -l "h_rt=$H_RT")
[ -n "$SGE_NODES" ] && RES+=(-l "hostname=$SGE_NODES")
# More than one thread per task => reserve that many cores on ONE node, otherwise
# SGE counts the task as 1 core and oversubscribes the node.
if [ "$SOLVER_THREADS" -gt 1 ]; then RES+=(-pe smp "$SOLVER_THREADS"); fi

# Pass the sample path to every task: each task starts a fresh shell on the
# compute node, so without -v a value set here would be lost there.
RES+=(-v "SAMPLE_CSV=$SAMPLE_CSV")

# Code version, computed ONCE here and stamped into every summary.json.
# "-dirty" = uncommitted changes: results would not be reproducible from git.
# (plain `git describe`: the cluster's git 1.8 has no --no-optional-locks; this runs
#  once, on the login node, so a short index lock is harmless)
CODE_VERSION=$(git describe --always --dirty 2>/dev/null || echo unknown)
RES+=(-v "MGPY2_CODE_VERSION=$CODE_VERSION")

echo "=========================================="
echo "MicroGridsPy country run (NEW engine)"
echo "Clusters:    $NUM_ROWS   (array 1-$NUM_ROWS, max $MAX_CONCURRENT concurrent)"
echo "Sample:      $SAMPLE_CSV"
echo "Solver:      $SOLVER (threads $SOLVER_THREADS, limit ${SOLVER_TIME_LIMIT}s, options ${SOLVER_OPTS:-<none>}) | horizon $HORIZON | mode $RUN_MODE"
echo "Export:      $EXPORT_PROFILE ($DISPATCH_FORMAT)"
echo "Code:        $CODE_VERSION"
if [[ "$CODE_VERSION" == *-dirty ]]; then echo "WARNING: uncommitted changes -> results not reproducible from git"; fi
echo "Feasibility: $FEASIBILITY_FLAGS"
echo "Resources:   ${RES[*]}"
echo "Env:         $CONDA_ENV @ $CONDA_BASE"
echo "Marker:      results/summary.json"
echo "=========================================="
read -p "Submit? (y/n) " -n 1 -r; echo ""
[[ $REPLY =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 0; }

mkdir -p logs
qsub "${RES[@]}" -t 1-"$NUM_ROWS" -tc "$MAX_CONCURRENT" hpc/submit_array.sh
echo "Submitted. Monitor with: ./hpc/monitor_jobs.sh"
