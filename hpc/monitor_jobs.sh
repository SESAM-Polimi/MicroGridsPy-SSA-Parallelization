#!/bin/bash
# Monitor NEW-engine array progress (marker: reporting_summary.csv)
CSV_FILE="advanced_sample.csv"
RESULTS_DIR="projects"
TOTAL=$(grep -v '^//' "$CSV_FILE" | tail -n +2 | wc -l | tr -d ' ')
COMPLETED=$(find "$RESULTS_DIR" -name "reporting_summary.csv" 2>/dev/null | wc -l | tr -d ' ')
echo "=========================================="
echo "Total clusters: $TOTAL | Completed: $COMPLETED | Remaining: $((TOTAL - COMPLETED))"
[ "$TOTAL" -gt 0 ] && echo "Progress: $(awk "BEGIN {printf \"%.1f%%\", ($COMPLETED/$TOTAL)*100}")"
echo "=========================================="
qstat | grep -E 'mgpy_array|mgpy_rerun' || echo "No active jobs found"
echo "Errors (clusters with error.txt):"
find "$RESULTS_DIR" -name error.txt 2>/dev/null | head -5
