"""Single source of truth for the cluster sample (advanced_sample.csv).

Task N (1-based, as used by SGE job arrays) ALWAYS means row N of
load_sample(path). Every script that maps task ids to clusters must use
this module, so the mapping cannot drift between scripts.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

ENV_VAR = "SAMPLE_CSV"


def sample_path(cli_value: str | None = None) -> Path:
    """Return the sample file: the CLI value if given, else $SAMPLE_CSV.

    There is deliberately NO silent default: a wrong default is exactly
    how a run can end up solving the wrong country.
    """
    value = cli_value or os.environ.get(ENV_VAR)
    if not value:
        raise SystemExit(f"No sample file given: pass --csv or set ${ENV_VAR}")
    path = Path(value)
    if not path.is_file():
        raise SystemExit(f"Sample file not found: {path}")
    return path


def load_sample(path: Path) -> pd.DataFrame:
    """Read the sample and drop '//' comment rows.

    The logic is identical to what run_cluster.py used for the August 2026
    Ethiopia run, so task ids keep the same meaning.
    The returned index runs 1..N and IS the task id.
    """
    df = pd.read_csv(path)
    is_comment = df.iloc[:, 0].astype(str).str.startswith("//")
    df = df[~is_comment].reset_index(drop=True)
    df.index = df.index + 1
    check_unique_ids(df, path)
    return df


def check_unique_ids(df: pd.DataFrame, path: Path | str = "sample") -> None:
    """Stop if a cluster id (cat) is missing or appears twice.

    Results are stored per cat (projects/<cat>/results), so two rows with the same id would
    write into the same folder: the second solve overwrites the first, or skip-if-done skips
    it, and one cluster silently disappears from the results.
    """
    if "cat" not in df.columns:
        raise SystemExit(f"{path}: no 'cat' column (cluster id).")
    ids = df["cat"].astype(str).str.strip()
    blank = df.index[ids.isin(["", "nan"])].tolist()
    if blank:
        raise SystemExit(f"{path}: rows without a cluster id (task ids {blank[:5]}).")
    repeated = ids[ids.duplicated(keep=False)]
    if not repeated.empty:
        first = repeated.groupby(repeated).apply(lambda s: s.index.tolist()).head(5).to_dict()
        raise SystemExit(f"{path}: {repeated.nunique()} cluster id(s) appear more than once, "
                         f"e.g. (cat: task ids) {first}. Fix the sample before running.")


def row_for_task(df: pd.DataFrame, task_id: int) -> dict:
    """Return the row for a 1-based task id, or stop with a clear error."""
    if not 1 <= task_id <= len(df):
        raise SystemExit(f"task id {task_id} out of range 1..{len(df)}")
    return df.loc[task_id].to_dict()


if __name__ == "__main__":
    # Used by the shell scripts:  python -m mgpy2.sample [path]
    # Prints the number of tasks, so bash never has to count rows itself.
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    print(len(load_sample(sample_path(arg))))