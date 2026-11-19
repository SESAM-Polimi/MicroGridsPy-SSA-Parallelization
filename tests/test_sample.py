"""mgpy2/sample.py: task ids and the unique-cluster-id check (checklist C13).

Run from the repo root:  python -m pytest tests/test_sample.py -q
"""
from __future__ import annotations

import pytest

from mgpy2.sample import load_sample, row_for_task


def _write(tmp_path, lines):
    path = tmp_path / "advanced_sample.csv"
    path.write_text("\n".join(["cat,lat,lon"] + lines) + "\n", encoding="utf-8")
    return path


def test_comment_rows_do_not_count_as_tasks(tmp_path):
    df = load_sample(_write(tmp_path, ["GHSL_1,1,1", "//GHSL_2,2,2", "SCHOOL_3,3,3"]))
    assert list(df.index) == [1, 2]
    assert row_for_task(df, 2)["cat"] == "SCHOOL_3"


def test_duplicate_cluster_id_stops(tmp_path):
    path = _write(tmp_path, ["GHSL_1,1,1", "GHSL_2,2,2", "GHSL_1,3,3"])
    with pytest.raises(SystemExit, match=r"appear more than once.*GHSL_1.*\[1, 3\]"):
        load_sample(path)


def test_duplicate_hidden_by_a_comment_is_fine(tmp_path):
    """A commented-out row is not a task, so it may repeat an id (e.g. an old copy kept for reference)."""
    df = load_sample(_write(tmp_path, ["GHSL_1,1,1", "//GHSL_1,1,1"]))
    assert len(df) == 1


def test_missing_cluster_id_stops(tmp_path):
    with pytest.raises(SystemExit, match="without a cluster id"):
        load_sample(_write(tmp_path, ["GHSL_1,1,1", ",2,2"]))
