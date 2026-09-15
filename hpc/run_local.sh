#!/bin/bash
# =====================================================================
# run_local.sh — LAPTOP test runner. Emulates the SGE array WITHOUT a
# scheduler: loops over the rows of a (small) sample CSV and
# runs the SAME mgpy2 pipeline used on the HPC, one cluster per "task".
#
# Use this to rehearse a country end-to-end on a handful of clusters
# before submitting the real array on the cluster.
#
# Usage (from repo root, in Git Bash):
#     PYTHON=/c/Users/<you>/anaconda3/envs/mgpy_planning/python.exe \
#     HORIZON=3 ./hpc/run_local.sh test_sample.csv
#   (without an argument it uses $SAMPLE_CSV)
#
# Env vars (all optional):
#   PYTHON             python to use (default: python on PATH)
#   SOLVER             highs (default) | gurobi
#   HORIZON            years (default 20; use 2-3 for a quick smoke test)
#   FEASIBILITY_FLAGS  "" (thesis PV+battery) | "--include-generator" | "--max-lost-load 0.05 --lost-load-cost 5.0"
# =====================================================================
set -uo pipefail   # no -e on purpose: one failed cluster must not stop the loop
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

CSV="${1:-${SAMPLE_CSV:?give a sample CSV as argument or set SAMPLE_CSV}}"
PYTHON="${PYTHON:-python}"
SOLVER="${SOLVER:-highs}"
HORIZON="${HORIZON:-20}"
FEASIBILITY_FLAGS="${FEASIBILITY_FLAGS:-}"

# Same row count / task mapping as the HPC runs (mgpy2/sample.py)
N=$("$PYTHON" -m mgpy2.sample "$CSV") || { echo "ERROR: cannot read sample $CSV"; exit 1; }
[ "$N" -ge 1 ] || { echo "ERROR: no data rows in $CSV"; exit 1; }

echo "=========================================="
echo "LOCAL test run (SGE-array emulation)"
echo "CSV=$CSV  clusters=$N  solver=$SOLVER  horizon=$HORIZON"
echo "feasibility='${FEASIBILITY_FLAGS:-<thesis PV+battery>}'"
echo "=========================================="
mkdir -p logs

ok=0; bad=0
for i in $(seq 1 "$N"); do
    echo "---- task $i / $N ----"
    if "$PYTHON" -m mgpy2.run_cluster --csv "$CSV" --task-id "$i" \
         --solver "$SOLVER" --horizon "$HORIZON" $FEASIBILITY_FLAGS; then
        ok=$((ok+1))
    else
        bad=$((bad+1)); echo "  (task $i failed — see projects/<cat>/results/error.txt)"
    fi
done

echo "=========================================="
echo "Local run done: $ok ok, $bad failed (of $N)."
echo "Aggregate results:"
echo "  $PYTHON -m mgpy2.postprocess --sample $CSV --out ${CSV%.csv}_results.csv"
echo "=========================================="
