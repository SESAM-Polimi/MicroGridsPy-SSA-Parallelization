#!/bin/bash
# =====================================================================
# Dynamic Job Array Submission Wrapper
# Counts rows in advanced_sample.csv and submits with correct range
# =====================================================================

CSV_FILE="advanced_sample.csv"

# Check CSV exists
if [ ! -f "$CSV_FILE" ]; then
    echo "ERROR: $CSV_FILE not found!"
    exit 1
fi

# Count data rows (skip header, skip comment lines starting with //)
NUM_ROWS=$(grep -v '^//' "$CSV_FILE" | tail -n +2 | wc -l | tr -d ' ')

if [ "$NUM_ROWS" -lt 1 ]; then
    echo "ERROR: No valid rows found in $CSV_FILE"
    exit 1
fi

echo "=========================================="
echo "MicroGridsPy Job Array Submission"
echo "=========================================="
echo "CSV file: $CSV_FILE"
echo "Total clusters to process: $NUM_ROWS"
echo "SGE array range: 1-$NUM_ROWS"
echo "Resources per task: 1 core, 3GB RAM, 3h limit"
echo "Nodes: node-1-3, node-1-6, node-1-7, node-1-8"
echo "Max concurrent tasks: 64 (Gurobi WLS: 250 sessions)"
echo "=========================================="
echo ""
read -p "Submit $NUM_ROWS jobs to SGE? (y/n) " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    # Create logs directory (SGE needs it before jobs start)
    mkdir -p logs
    
    # Submit job array with dynamic range
    # -tc 64: limits to 64 concurrent tasks (4 nodes × 16 CPUs, Gurobi WLS 250 sessions)
    qsub -t 1-$NUM_ROWS -tc 64 submit_array.sh
    
    echo ""
    echo "✓ Job array submitted!"
    echo "Monitor progress with: ./monitor_jobs.sh"
    echo "Check logs in: logs/"
else
    echo "Submission cancelled."
fi
