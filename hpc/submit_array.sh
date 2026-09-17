#!/bin/bash
#$ -S /bin/bash
#$ -N mgpy_array
#$ -cwd
#$ -j y
#$ -o /dev/null   # all output goes to logs/ (see exec below); avoids 1 empty SGE file per task

set -euo pipefail

: "${SGE_O_WORKDIR:?ERROR: SGE_O_WORKDIR is not defined}"

REPO_ROOT="$SGE_O_WORKDIR"
cd "$REPO_ROOT"
source "$REPO_ROOT/hpc/env.sh"
check_disk_or_hold "$REPO_ROOT"

mkdir -p "$REPO_ROOT/logs"

# Job ID in the name: a rerun never overwrites the evidence of an earlier run
PADDED=$(printf "%05d" "$SGE_TASK_ID")
exec >"$REPO_ROOT/logs/${JOB_NAME}.${JOB_ID}.${PADDED}.log" 2>&1

echo "============================================================"
echo "Start time:        $(date)"
echo "Task ID:           $SGE_TASK_ID"
echo "Node:              $(hostname)"
echo "Repository root:   $REPO_ROOT"
echo "Working directory: $(pwd)"
echo "SGE workdir:       $SGE_O_WORKDIR"
echo "============================================================"

activate_env

echo "Conda environment: $CONDA_ENV"
echo "Python executable: $(command -v python)"
echo "Run mode:          $RUN_MODE"
echo "Solver:            $SOLVER (threads=$SOLVER_THREADS, time limit=${SOLVER_TIME_LIMIT}s)"
echo "Solver options:    ${SOLVER_OPTS:-<none>}"
echo "Projects dir:      ${MGPY2_PROJECTS_DIR:-<repo>/projects}"
echo "Slots (NSLOTS):    ${NSLOTS:-1}"
echo "Horizon:           $HORIZON"

build_opt_flags   # -> OPT_FLAGS (hpc/env.sh)
echo "Sample:            $SAMPLE_CSV"
echo "Export profile:    $EXPORT_PROFILE ($DISPATCH_FORMAT)"

python -m mgpy2.run_cluster \
    --task-id "$SGE_TASK_ID" \
    --csv "$SAMPLE_CSV" \
    --solver "$SOLVER" \
    --threads "$SOLVER_THREADS" \
    --time-limit "$SOLVER_TIME_LIMIT" \
    ${OPT_FLAGS[@]+"${OPT_FLAGS[@]}"} \
    --horizon "$HORIZON" \
    --export-profile "$EXPORT_PROFILE" \
    --dispatch-format "$DISPATCH_FORMAT" \
    $FEASIBILITY_FLAGS \
    $(mode_flag)
    
echo "Task completed at $(date)"
