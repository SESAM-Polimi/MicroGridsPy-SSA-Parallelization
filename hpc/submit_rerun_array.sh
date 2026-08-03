#!/bin/bash
#$ -S /bin/bash
#$ -N mgpy_rerun
#$ -cwd
#$ -j y
# =====================================================================
# SGE array TASK for RERUNS. Maps SGE_TASK_ID -> the Nth line of
# tasks_failed.txt (a real cluster task-id) and reruns it.
# Generate the list first:  python hpc/make_failed_task_list.py
# Then submit (from repo root), e.g.:
#     source hpc/env.sh
#     M=$(wc -l < tasks_failed.txt)
#     qsub -q "$SGE_QUEUE" -l h_vmem=$H_VMEM -l h_rt=$H_RT \
#          $( [ -n "$SGE_NODES" ] && echo -l hostname=$SGE_NODES ) \
#          -t 1-$M -tc $MAX_CONCURRENT hpc/submit_rerun_array.sh
# =====================================================================
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
source hpc/env.sh
activate_env

mkdir -p logs
PADDED=$(printf "%05d" "$SGE_TASK_ID")
exec >"logs/${JOB_NAME}.${PADDED}.log" 2>&1

[ -f tasks_failed.txt ] || { echo "tasks_failed.txt not found. Run: python hpc/make_failed_task_list.py"; exit 1; }
ACTUAL_TASK_ID=$(sed -n "${SGE_TASK_ID}p" tasks_failed.txt)
[ -n "$ACTUAL_TASK_ID" ] || { echo "Invalid array index $SGE_TASK_ID"; exit 1; }

echo "[$(date)] rerun array idx=$SGE_TASK_ID -> cluster task=$ACTUAL_TASK_ID node=$(hostname)"
python -m mgpy2.run_cluster --task-id "$ACTUAL_TASK_ID" \
    --solver "$SOLVER" --horizon "$HORIZON" $FEASIBILITY_FLAGS $(mode_flag)
