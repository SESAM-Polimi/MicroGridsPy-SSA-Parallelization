#!/usr/bin/env python3
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "advanced_sample.csv"
PROJECTS_DIR = ROOT / "projects"
OUT_PATH = ROOT / "tasks_failed.txt"

# Read CSV and map cat -> 1-based task index (excluding comment rows)
cat_to_task = {}
with open(CSV_PATH, newline='') as f:
    reader = csv.DictReader(row for row in f if not str(row).lstrip().startswith('//'))
    # DictReader consumes header from the first non-comment line
    # Re-open to count indices correctly

# We need line-based counting with header skip and comment filtering
rows = []
with open(CSV_PATH, newline='') as f:
    raw = list(f)

# Identify header line (first non-comment)
header_idx = None
for i, line in enumerate(raw):
    if not line.strip().startswith('//'):
        header_idx = i
        break
if header_idx is None:
    raise SystemExit("No header found in advanced_sample.csv")

header = [c.strip() for c in raw[header_idx].strip().split(',')]
try:
    cat_idx = header.index('cat')
except ValueError:
    raise SystemExit("Column 'cat' not found in advanced_sample.csv header")

# 1-based tasks start after header; skip comment rows
current_task = 0
for line in raw[header_idx+1:]:
    if line.strip().startswith('//'):
        continue
    current_task += 1
    cols = [c.strip() for c in line.rstrip('\n').split(',')]
    if cat_idx < len(cols):
        cat_to_task[cols[cat_idx]] = current_task

# Determine failed projects (no costs.csv)
failed_tasks = []
if PROJECTS_DIR.exists():
    for proj in sorted(PROJECTS_DIR.iterdir()):
        if not proj.is_dir():
            continue
        cat = proj.name
        if not (proj / 'results' / 'costs.csv').exists():
            task_id = cat_to_task.get(cat)
            if task_id is not None:
                failed_tasks.append(task_id)

failed_tasks = sorted(set(failed_tasks))

if not failed_tasks:
    print("No failed clusters detected (all have results/costs.csv).")
    OUT_PATH.write_text("")
else:
    OUT_PATH.write_text("\n".join(str(x) for x in failed_tasks) + "\n")
    print(f"Wrote {len(failed_tasks)} failed task IDs to {OUT_PATH.name}")
