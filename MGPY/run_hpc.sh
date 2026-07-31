#!/bin/bash
#$ -N mgpy_1768
#$ -q hub.q
#$ -pe mpi 32
#$ -l h_vmem=6G
#$ -l h_rt=08:00:00
#$ -o mgpy_$JOB_ID.log
#$ -e mgpy_$JOB_ID.err
#$ -cwd
#$ -V

# Print job info
echo "=========================================="
echo "Job started at: $(date)"
echo "Running on node: $(hostname)"
echo "CPUs allocated: $NSLOTS"
echo "Memory: 32 × 6G = 192GB"
echo "Queue: hub.q"
echo "=========================================="
echo ""

# Load modules if available (check with: module avail)
# module load miniconda3
# module load gurobi

# Initialize conda and activate environment
eval "$(/home/energia/mpieraccini/miniconda3/bin/conda shell.bash hook)"
conda activate MGPY

# Verify environment
echo "Python: $(which python)"
echo "Python version: $(python --version)"
echo ""

# Set Gurobi license (site license)
unset GRB_WLSACCESSID GRB_WLSSECRET GRB_LICENSEID
export GRB_LICENSE_FILE=/home/energia/mpieraccini/gurobi.lic

# Already in correct directory due to -cwd directive
# (SGE -cwd sets working directory to submission directory)

# Verify files exist
echo "Checking files..."
if [ ! -f "advanced_sample.csv" ]; then
    echo "ERROR: advanced_sample.csv not found!"
    exit 1
fi
echo "✓ advanced_sample.csv found ($(wc -l < advanced_sample.csv) lines)"

if [ ! -f "MGPY/orchestrator_subprocess.py" ]; then
    echo "ERROR: MGPY/orchestrator_subprocess.py not found!"
    exit 1
fi
echo "✓ orchestrator.py found"

if [ ! -f "MGPY/run_nostreamlit_update.py" ]; then
    echo "ERROR: MGPY/run_nostreamlit_update.py not found!"
    exit 1
fi
echo "✓ run_nostreamlit_update.py found"
echo ""

# Run orchestrator with subprocess isolation
echo "Starting optimization with subprocess-isolated workers..."
python MGPY/orchestrator_subprocess.py

# Job completion
echo ""
echo "=========================================="
echo "Job completed at: $(date)"

# Count completed clusters
COMPLETED=$(find projects/*/results/costs.csv 2>/dev/null | wc -l)
TOTAL=$(tail -n +2 advanced_sample.csv | wc -l)
echo "Clusters completed: $COMPLETED / $TOTAL"
echo "=========================================="
