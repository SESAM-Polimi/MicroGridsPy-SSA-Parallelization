"""
Central path resolution for the migration pipeline.

All locations are resolved relative to the repository root by default and can be
overridden with environment variables so the same code runs unchanged on the
local Windows machine and on the HPC:

  MGPY2_REPO_ROOT     repo root (contains this package)              [auto-detected]
  MGPY2_NEW_ENGINE    dir of the NEW engine (package `core`)         [default: <repo>/engines/new]
  MGPY2_OLD_ENGINE    dir of the OLD engine (package `microgridspy`) [default: <repo>/engines/old]
  MGPY2_PROJECTS_DIR  where per-cluster projects live                [default: <repo>/projects]
  MGPY2_DATA_SHEET    dir with School_weights.csv etc.               [default: <repo>/data/data_sheet]

Import side effect: calling `ensure_engines_importable()` puts both engine dirs
on sys.path. The NEW engine additionally requires the process CWD to be its
parent-of-`projects` because `core.io.utils.project_paths` resolves projects
against `Path.cwd()`. Use `projects_root()` / `run_cwd()` accordingly.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("MGPY2_REPO_ROOT")
    if env:
        return Path(env).resolve()
    # this file is <repo>/mgpy2/paths.py
    return Path(__file__).resolve().parent.parent


def new_engine_dir() -> Path:
    env = os.environ.get("MGPY2_NEW_ENGINE")
    return Path(env).resolve() if env else (repo_root() / "engines" / "new")


def old_engine_dir() -> Path:
    env = os.environ.get("MGPY2_OLD_ENGINE")
    return Path(env).resolve() if env else (repo_root() / "engines" / "old")


def projects_root() -> Path:
    env = os.environ.get("MGPY2_PROJECTS_DIR")
    return Path(env).resolve() if env else (repo_root() / "projects")


def data_sheet_dir() -> Path:
    env = os.environ.get("MGPY2_DATA_SHEET")
    return Path(env).resolve() if env else (repo_root() / "data" / "data_sheet")


def school_weights_csv() -> Path:
    return data_sheet_dir() / "School_weights.csv"


def ensure_engines_importable() -> None:
    """Put both engine directories on sys.path (idempotent)."""
    for d in (new_engine_dir(), old_engine_dir()):
        s = str(d)
        if d.exists() and s not in sys.path:
            sys.path.insert(0, s)
