#!/bin/bash
#$ -S /bin/bash
#$ -N mgpy_rerun
#$ -q energia.q
#$ -cwd
#$ -j y

# Activate conda
eval "$(/home/energia/mpieraccini/miniconda3/bin/conda shell.bash hook)"
conda activate mgpy_clean
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

# HiGHS threading
export OMP_NUM_THREADS=1

# Resources (3GB per task, 64 concurrent, assigned nodes only)
#$ -l h_vmem=3G
#$ -l h_rt=03:00:00
#$ -l hostname=node-1-3|node-1-6|node-1-7|node-1-8

# Ensure logs dir and zero-padded logs
mkdir -p logs
PADDED_TASK_ID=$(printf "%05d" "$SGE_TASK_ID")
LOG_FILE="logs/${JOB_NAME}.${PADDED_TASK_ID}.log"
exec >"$LOG_FILE" 2>&1

# Expect tasks_failed.txt in CWD
if [ ! -f tasks_failed.txt ]; then
  echo "tasks_failed.txt not found. Generate it with: python make_failed_task_list.py"
  exit 1
fi

NUM_TASKS=$(wc -l < tasks_failed.txt | tr -d ' ')
if [ -z "$NUM_TASKS" ] || [ "$NUM_TASKS" -eq 0 ]; then
  echo "No tasks to rerun."
  exit 0
fi

# Map array index to actual task-id
ACTUAL_TASK_ID=$(sed -n "${SGE_TASK_ID}p" tasks_failed.txt)
if [ -z "$ACTUAL_TASK_ID" ]; then
  echo "Invalid array index $SGE_TASK_ID"
  exit 1
fi

# Run the single-cluster with the mapped task-id
python run_single_cluster.py --task-id "$ACTUAL_TASK_ID"
