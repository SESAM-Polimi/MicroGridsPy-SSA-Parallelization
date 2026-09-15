#!/bin/bash
#$ -S /bin/bash
#$ -N mgpy_array
#$ -cwd
#$ -j y

set -euo pipefail

: "${SGE_O_WORKDIR:?ERROR: SGE_O_WORKDIR is not defined}"

REPO_ROOT="$SGE_O_WORKDIR"
cd "$REPO_ROOT"

mkdir -p "$REPO_ROOT/logs"

PADDED=$(printf "%05d" "$SGE_TASK_ID")
exec >"$REPO_ROOT/logs/${JOB_NAME}.${PADDED}.log" 2>&1

echo "============================================================"
echo "Start time:        $(date)"
echo "Task ID:           $SGE_TASK_ID"
echo "Node:              $(hostname)"
echo "Repository root:   $REPO_ROOT"
echo "Working directory: $(pwd)"
echo "SGE workdir:       $SGE_O_WORKDIR"
echo "============================================================"

source "$REPO_ROOT/hpc/env.sh"
activate_env

echo "Conda environment: $CONDA_ENV"
echo "Python executable: $(command -v python)"
echo "Run mode:          $RUN_MODE"
echo "Solver:            $SOLVER"
echo "Horizon:           $HORIZON"
echo "Sample:            $SAMPLE_CSV"

python -m mgpy2.run_cluster \
    --task-id "$SGE_TASK_ID" \
    --csv "$SAMPLE_CSV" \
    --solver "$SOLVER" \
    --horizon "$HORIZON" \
    $FEASIBILITY_FLAGS \
    $(mode_flag)
    
echo "Task completed at $(date)"
