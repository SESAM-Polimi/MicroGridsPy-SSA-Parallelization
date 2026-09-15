#!/bin/bash
#$ -S /bin/bash
#$ -N mgpy_rerun
#$ -cwd
#$ -j y
#$ -o /dev/null   # all output goes to logs/ (see exec below)
# =====================================================================
# SGE array task for RERUNS.
# Array index i -> line i of tasks_failed.txt, which holds a real task id
# (a row of $SAMPLE_CSV, see mgpy2/sample.py).
#
# From the REPO ROOT:
#   source hpc/env.sh && activate_env
#   python hpc/make_failed_task_list.py        # writes tasks_failed.txt
#   M=$(wc -l < tasks_failed.txt)
#   qsub -q "$SGE_QUEUE" -l h_vmem="$H_VMEM" -l h_rt="$H_RT" \
#        -l hostname="$SGE_NODES" -v SAMPLE_CSV="$SAMPLE_CSV" \
#        -t 1-"$M" -tc "$MAX_CONCURRENT" hpc/submit_rerun_array.sh
# =====================================================================
set -euo pipefail

: "${SGE_O_WORKDIR:?ERROR: SGE_O_WORKDIR is not defined}"
REPO_ROOT="$SGE_O_WORKDIR"     # NOT $0: under SGE, $0 is a copy in the spool dir
cd "$REPO_ROOT"
source hpc/env.sh
check_disk_or_hold "$REPO_ROOT"

mkdir -p logs
PADDED=$(printf "%05d" "$SGE_TASK_ID")
exec >"logs/${JOB_NAME}.${JOB_ID}.${PADDED}.log" 2>&1

activate_env

[ -f tasks_failed.txt ] || { echo "tasks_failed.txt not found. Run: python hpc/make_failed_task_list.py"; exit 1; }
ACTUAL_TASK_ID=$(sed -n "${SGE_TASK_ID}p" tasks_failed.txt)
[ -n "$ACTUAL_TASK_ID" ] || { echo "Invalid array index $SGE_TASK_ID"; exit 1; }

echo "[$(date)] rerun idx=$SGE_TASK_ID -> task=$ACTUAL_TASK_ID node=$(hostname)"
echo "Sample: $SAMPLE_CSV | Solver: $SOLVER | Horizon: $HORIZON"

python -m mgpy2.run_cluster \
    --task-id "$ACTUAL_TASK_ID" \
    --csv "$SAMPLE_CSV" \
    --solver "$SOLVER" \
    --threads "$SOLVER_THREADS" \
    --time-limit "$SOLVER_TIME_LIMIT" \
    --horizon "$HORIZON" \
    $FEASIBILITY_FLAGS \
    $(mode_flag)

echo "[$(date)] rerun task $ACTUAL_TASK_ID completed"