#!/bin/bash
#$ -S /bin/bash
#$ -N mgpy_array
#$ -q energia.q
#$ -cwd
#$ -j y
# We'll handle log redirection inside the script to allow zero-padded task IDs

# =====================================================================
# SGE Job Array Submission Script (DYNAMIC)
# Auto-detects number of rows in advanced_sample.csv
# =====================================================================

# Activate conda environment (same pattern as run_hpc.sh)
eval "$('/home/energia/mpieraccini/miniconda3/bin/conda' shell.bash hook)"
conda activate mgpy_clean
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

# Using HiGHS solver (no license needed, unlimited concurrency)
# Limit HiGHS to 1 thread per task to avoid oversubscription
export OMP_NUM_THREADS=1

# Resource configuration per task
# - 1 core per cluster (SGE default when no -pe specified)
# - 3GB RAM per task (Gurobi WLS: 250 sessions available)
# - 3 hours time limit per cluster
# - Restrict to assigned nodes: 1-3, 1-6, 1-7, 1-8
#$ -l h_vmem=3G
#$ -l h_rt=03:00:00
#$ -l hostname=node-1-3|node-1-6|node-1-7|node-1-8

# Create log directory if missing
mkdir -p logs

# Redirect stdout/stderr to a zero-padded per-task log file (e.g., 00009)
PADDED_TASK_ID=$(printf "%05d" "$SGE_TASK_ID")
LOG_FILE="logs/${JOB_NAME}.${PADDED_TASK_ID}.log"
exec >"$LOG_FILE" 2>&1

# Keep logs minimal; suppress verbose banners

# Run single cluster using SGE_TASK_ID
python run_single_cluster.py --task-id $SGE_TASK_ID

# End of task
