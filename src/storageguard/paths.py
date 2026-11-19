"""Repository-relative paths (overridable with the ``SG_ROOT`` environment variable)."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("SG_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "configs").exists():
            return parent
    return Path.cwd()


def config_dir() -> Path:
    return repo_root() / "configs"


def bench_dir() -> Path:
    return repo_root() / "data" / "bench"


def results_dir() -> Path:
    return repo_root() / "results"


def paper_dir() -> Path:
    return repo_root() / "paper"
