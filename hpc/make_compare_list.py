"""
Pick a small, REPRODUCIBLE set of clusters to compare engine versions
(e.g. the Aug-2026 run vs a new run), and write it as a task list for
hpc/submit_rerun_array.sh.

Selection per category that has earlier results: the LCOE and system-size
extremes plus a seeded random draw. Categories without earlier results get
a random draw only. A few fixed benchmark clusters are always included.

Run from the REPO ROOT with the env active (login node is fine, it only reads text):
    python hpc/make_compare_list.py --aug ~/eth_aug2026_obj_lcoe.txt --out compare_tasks.txt

Output: one line per task, "<task>\t<cat>\t<why>", sorted by task.
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path

import pandas as pd

# `python hpc/x.py` puts hpc/ (not the repo root) on sys.path: add the root
# so `mgpy2` is importable, as in make_failed_task_list.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mgpy2.sample import load_sample, sample_path  # noqa: E402

# "<task> <seconds> [<cat>] ok obj=<x> lcoe=<y>"  (other statuses are skipped)
AUG_LINE = re.compile(r"^(\d+)\s+([\d.]+)\s+\[(\S+)\]\s+ok\s+obj=(\S+)\s+lcoe=(\S+)")

# Always included: the September test cluster and the thread-benchmark clusters.
FIXED = {563: "Sep test cluster", 12426: "benchmark p10", 6781: "benchmark p50",
         5028: "benchmark p90", 8179: "benchmark p100 (hardest)"}

# (extremes per criterion, random draws) per category with earlier results
PLAN = {"GHSL": (3, 9), "SCHOOL": (2, 7)}
RANDOM_ONLY = 10   # categories with no earlier results


def read_aug(path: Path) -> pd.DataFrame:
    rows = []
    for line in path.expanduser().read_text().splitlines():
        m = AUG_LINE.match(line)
        if m:
            rows.append({"task": int(m[1]), "solve_s": float(m[2]), "cat": m[3],
                         "obj": float(m[4]), "lcoe": float(m[5])})
    if not rows:
        raise SystemExit(f"no 'ok' rows parsed from {path}")
    return pd.DataFrame(rows)


def kind_of(cat: pd.Series) -> pd.Series:
    """'GHSL_563' -> 'GHSL'"""
    return cat.astype(str).str.split("_").str[0]


def pick(df: pd.DataFrame, n_ext: int, n_rand: int, rng: random.Random) -> dict[int, str]:
    chosen: dict[int, str] = {}

    def add(sub: pd.DataFrame, why: str) -> None:
        for t in sub["task"]:
            chosen.setdefault(int(t), why)      # first reason wins

    add(df.nsmallest(n_ext, "lcoe"), "lowest LCOE")
    add(df.nlargest(n_ext, "lcoe"), "highest LCOE")
    add(df.nlargest(n_ext, "obj"), "largest system (NPC)")
    add(df.nsmallest(n_ext, "obj"), "smallest system (NPC)")
    rest = sorted(set(df["task"]) - set(chosen))  # sorted => same draw on every machine
    for t in rng.sample(rest, min(n_rand, len(rest))):
        chosen.setdefault(int(t), "random")
    return chosen


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aug", required=True, type=Path, help="earlier results: task secs [cat] ok obj= lcoe=")
    ap.add_argument("--out", default="compare_tasks.txt", type=Path)
    ap.add_argument("--csv", default=None, help="sample file (default: $SAMPLE_CSV)")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    sample = load_sample(sample_path(args.csv))
    sample_kind = kind_of(sample["cat"])
    aug = read_aug(args.aug)

    # Guard: the earlier run must refer to the SAME rows. If the sample was
    # regenerated or reordered, task ids no longer mean the same cluster.
    now = sample.loc[aug["task"], "cat"].astype(str).to_numpy()
    bad = (now != aug["cat"].to_numpy()).sum()
    if bad:
        raise SystemExit(f"{bad} tasks map to a different cat than in {args.aug}: sample changed?")

    rng = random.Random(args.seed)   # own generator: reproducible, no global state
    chosen: dict[int, str] = dict(FIXED)
    aug["kind"] = kind_of(aug["cat"])
    for kind, (n_ext, n_rand) in PLAN.items():
        for t, why in pick(aug[aug["kind"] == kind], n_ext, n_rand, rng).items():
            chosen.setdefault(t, why)
    for kind in sorted(set(sample_kind) - set(aug["kind"])):
        tasks = sorted(sample.index[sample_kind == kind])
        for t in rng.sample(tasks, min(RANDOM_ONLY, len(tasks))):
            chosen.setdefault(int(t), "random (no earlier result)")

    lines = [f"{t}\t{sample.loc[t, 'cat']}\t{why}" for t, why in sorted(chosen.items())]
    args.out.write_text("\n".join(lines) + "\n")
    counts = pd.Series([kind_of(pd.Series([sample.loc[t, 'cat']]))[0] for t in chosen]).value_counts()
    print(f"wrote {len(lines)} tasks to {args.out}:", counts.to_dict())


if __name__ == "__main__":
    main()
