#!/bin/bash
#$ -S /bin/bash
#$ -N mgpy_array
#$ -cwd
#$ -j y
# =====================================================================
# SGE array TASK — one row of advanced_sample.csv per SGE_TASK_ID.
# Resources (queue, memory, runtime, nodes, concurrency) are set on the
# qsub command line by submit_jobs.sh, NOT here — SGE ignores #$ options
# that appear after the first shell command, which is a classic footgun.
# =====================================================================
set -uo pipefail

# Resolve repo root (parent of hpc/) so this works regardless of cwd.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
source hpc/env.sh
activate_env

mkdir -p logs
PADDED=$(printf "%05d" "$SGE_TASK_ID")
exec >"logs/${JOB_NAME}.${PADDED}.log" 2>&1

echo "[$(date)] task=$SGE_TASK_ID node=$(hostname) env=$CONDA_ENV mode=$RUN_MODE"
python -m mgpy2.run_cluster --task-id "$SGE_TASK_ID" \
    --solver "$SOLVER" --horizon "$HORIZON" $FEASIBILITY_FLAGS $(mode_flag)
