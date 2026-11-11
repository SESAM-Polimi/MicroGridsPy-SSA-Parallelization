#!/usr/bin/env python
"""Pre-flight check of the sample files: every cluster with households needs a cooling class.

    python hpc/check_samples.py                      # data/sample_input_2025/*/advanced_sample.csv
    python hpc/check_samples.py path/to/samples      # any folder of *.csv, searched recursively

Exit status 1 if any household cluster lacks a valid class (NC, AY, OM, AS), so the check can
gate a submission. Run it before every production run and after regenerating samples.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

VALID = ("NC", "AY", "OM", "AS")
TIERS = [f"h_tier{t}" for t in range(1, 6)]


def check(path: Path) -> dict:
    # keep_default_na=False: pandas would otherwise turn the text "NA" into a missing value
    d = pd.read_csv(path, low_memory=False, keep_default_na=False, comment="/")
    households = d[TIERS].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    cooling = d["cooling"].astype(str).str.strip()
    populated = households > 0
    bad = populated & ~cooling.isin(VALID)
    shares = cooling[populated].value_counts(normalize=True)
    return {"file": str(path), "household_clusters": int(populated.sum()), "without_class": int(bad.sum()),
            **{c: round(100 * float(shares.get(c, 0.0)), 1) for c in VALID},
            "other_values": sorted(set(cooling[bad]))[:5]}


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1] / "data" / "sample_input_2025"
    files = sorted(root.rglob("*.csv"))
    if not files:
        sys.exit(f"no csv files under {root}")
    table = pd.DataFrame([check(f) for f in files])
    table["file"] = table["file"].str.replace(str(root) + "/", "", regex=False)
    print(table.to_string(index=False))
    bad, total = int(table.without_class.sum()), int(table.household_clusters.sum())
    print(f"\nhousehold clusters without a valid cooling class: {bad:,} of {total:,}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
