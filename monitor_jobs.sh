#!/bin/bash
# =====================================================================
# Monitor SGE job array progress
# =====================================================================

CSV_FILE="advanced_sample.csv"
RESULTS_DIR="projects"

# Count total clusters
TOTAL=$(grep -v '^//' "$CSV_FILE" | tail -n +2 | wc -l | tr -d ' ')

# Count completed results (folders with costs.csv)
COMPLETED=$(find "$RESULTS_DIR" -name "costs.csv" 2>/dev/null | wc -l | tr -d ' ')

echo "=========================================="
echo "MicroGridsPy Job Array Progress"
echo "=========================================="
echo "Total clusters: $TOTAL"
echo "Completed: $COMPLETED"
echo "Remaining: $((TOTAL - COMPLETED))"
echo "Progress: $(awk "BEGIN {printf \"%.1f%%\", ($COMPLETED/$TOTAL)*100}")"
echo "=========================================="
echo ""

# Show active/pending jobs
echo "SGE Queue Status:"
qstat | grep mgpy_array || echo "No active jobs found"
echo ""

# Show recent errors (last 5)
echo "Recent Errors (last 5):"
if ls logs/*.log 1> /dev/null 2>&1; then
    grep -l "FAILED\|ERROR\|Exception" logs/*.log 2>/dev/null | tail -5 | while read log; do
        echo "  - $log"
    done
else
    echo "  No log files found yet"
fi
echo ""

# Suggest next steps
if [ "$COMPLETED" -eq "$TOTAL" ]; then
    echo "✓ All clusters completed!"
    echo "Results saved in: $RESULTS_DIR/*/results/"
elif [ "$COMPLETED" -gt 0 ]; then
    echo "Jobs still running. Re-run this script to check progress."
else
    echo "No completions yet. Jobs may still be queued."
    echo "Check queue with: qstat"
fi
