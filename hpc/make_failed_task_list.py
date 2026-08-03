#!/usr/bin/env python3
"""
make_failed_task_list_new.py — list SGE task IDs whose NEW-engine results are missing.

A cluster is 'done' when projects/<cat>/results/reporting_summary.csv exists
(replaces the old costs.csv marker). Writes 1-based task IDs to tasks_failed.txt
for rerun via submit_rerun_array_new.sh.
"""
from __future__ import annotations

import csv
from pathlib import Path

from mgpy2.paths import projects_root, repo_root

ROOT = repo_root()                       # repo root, regardless of this file's location
CSV_PATH = ROOT / "advanced_sample.csv"
OUT_PATH = ROOT / "tasks_failed.txt"
MARKER = "reporting_summary.csv"


def main() -> None:
    projects = projects_root()
    # map cat -> 1-based task index (skip // comment rows)
    cat_to_task = {}
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = [ln for ln in f]
    header_idx = next(i for i, ln in enumerate(rows) if not ln.strip().startswith("//"))
    header = [c.strip() for c in rows[header_idx].strip().split(",")]
    cat_idx = header.index("cat")
    task = 0
    for ln in rows[header_idx + 1:]:
        if ln.strip().startswith("//") or not ln.strip():
            continue
        task += 1
        cols = [c.strip() for c in ln.rstrip("\n").split(",")]
        if cat_idx < len(cols):
            cat_to_task[cols[cat_idx]] = task

    failed = sorted({
        t for cat, t in cat_to_task.items()
        if not (projects / cat / "results" / MARKER).exists()
    })
    OUT_PATH.write_text(("\n".join(map(str, failed)) + "\n") if failed else "", encoding="utf-8")
    print(f"{len(failed)} failed/incomplete tasks -> {OUT_PATH.name}"
          if failed else "All clusters have results.")


if __name__ == "__main__":
    main()
