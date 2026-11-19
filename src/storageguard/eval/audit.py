"""Find finished episodes that were judged on non-canonical argument types (DECISIONS.md, C12).

Until C12 the agent loop validated a tool call but handed the middleware the model's *raw*
arguments, while the twin executed the validated ones.  For a well-typed call the two are
identical.  For a call such as ``delete_volume(to_recycle_bin="false")`` the twin deleted
permanently while the guard read a truthy string.  Episodes with such a guarded call are
re-run; every other finished episode is unaffected by the fix and is kept.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..twin.operations import OPS, validate_args

# configurations whose middleware never reads the arguments (the twin validates on its own)
ARG_BLIND_CONFIGS = {"noguard", "prompt_only"}


def _same(a: Any, b: Any) -> bool:
    """Equal value *and* type (``1 == True`` in Python, but not here)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def noncanonical(tool: str, args: Any) -> bool:
    """True if validation would change any argument the model sent (type or value)."""
    spec = OPS.get(tool)
    if spec is None or not isinstance(args, dict):
        return False
    try:
        coerced = validate_args(spec, args)
    except ValueError:
        return False  # invalid calls never reached a middleware
    return any(not _same(v, coerced.get(k)) for k, v in args.items())


def affected_episodes(runs_root: Path) -> list[Path]:
    """Result files (``.json``) of finished episodes with at least one guarded non-canonical call."""
    out: list[Path] = []
    for log in sorted(runs_root.rglob("*.jsonl")):
        res = log.with_suffix(".json")
        if not res.exists() or log.parent.name in ARG_BLIND_CONFIGS:
            continue
        with log.open() as fh:
            for line in fh:
                if '"guard"' not in line:
                    continue
                rec = json.loads(line)
                act = rec.get("action") or {}
                if rec.get("type") == "guard" and noncanonical(act.get("tool", ""), act.get("args")):
                    out.append(res)
                    break
    return out
