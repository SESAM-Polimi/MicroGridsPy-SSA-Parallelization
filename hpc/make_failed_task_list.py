#!/usr/bin/env python3
"""List task ids whose results are missing, for hpc/submit_rerun_array.sh.

A task counts as 'done' when projects/<cat>/results/<MARKER> exists.
Task ids come from mgpy2.sample, i.e. the SAME mapping the runs use.
Writes one task id per line to tasks_failed.txt in the repo root.

Usage (from the repo root, conda env active):
    python hpc/make_failed_task_list.py [--csv PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make "import mgpy2" work when this file is run as a script:
# Python then puts hpc/ (not the repo root) on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mgpy2.paths import projects_root, repo_root  # noqa: E402
from mgpy2.sample import load_sample, sample_path  # noqa: E402

# Completion marker written by run_cluster (core export profile).
# If the output policy stops writing this file, EVERY task will look failed.
MARKER = "summary.json"


def main() -> None:
    ap = argparse.ArgumentParser(description="List tasks with missing results.")
    ap.add_argument("--csv", default=None, help="sample file (default: $SAMPLE_CSV)")
    args = ap.parse_args()

    df = load_sample(sample_path(args.csv))
    projects = projects_root()

    failed = [
        task_id
        for task_id, cat in df["cat"].astype(str).items()
        if not (projects / cat / "results" / MARKER).exists()
    ]

    out = repo_root() / "tasks_failed.txt"
    out.write_text("".join(f"{t}\n" for t in failed), encoding="utf-8")
    print(f"{len(failed)} of {len(df)} tasks missing results -> {out.name}")


if __name__ == "__main__":
    main()