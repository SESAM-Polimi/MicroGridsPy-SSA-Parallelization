#!/bin/bash
# =====================================================================
# Progress of the current country run. From the repo root:
#     bash hpc/monitor_jobs.sh
# "Done" marker: projects/<cat>/results/reporting_summary.csv
# (TODO plan 4.2: switch to status.json)
# =====================================================================
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"   # OK here: run by hand, not via SGE
cd "$REPO_ROOT"
source hpc/env.sh
activate_env

MARKER="reporting_summary.csv"
TOTAL=$(python -m mgpy2.sample "$SAMPLE_CSV")

# -mindepth/-maxdepth 3: only look at projects/<cat>/results/<file>
# instead of walking every file of every project (millions of files).
# "|| true": a missing projects/ folder must not abort the script (pipefail).
COMPLETED=$(find projects -mindepth 3 -maxdepth 3 -path "*/results/$MARKER" 2>/dev/null | wc -l || true)
ERRORS=$(find projects -mindepth 3 -maxdepth 3 -path "*/results/error.txt" 2>/dev/null | wc -l || true)

echo "=========================================="
echo "Sample:    $SAMPLE_CSV"
echo "Total: $TOTAL | Completed: $COMPLETED | Errors: $ERRORS | Remaining: $((TOTAL - COMPLETED))"
awk -v c="$COMPLETED" -v t="$TOTAL" 'BEGIN { if (t > 0) printf "Progress:  %.1f%%\n", 100 * c / t }'
echo "=========================================="
qstat -u "$USER" | grep -E 'mgpy_array|mgpy_rerun' || echo "No active jobs found"
echo "First errors:"
find projects -mindepth 3 -maxdepth 3 -path "*/results/error.txt" 2>/dev/null | head -5 || true