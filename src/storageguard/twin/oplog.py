"""Append-only operation log with before/after diffs, and log replay."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class OpLogEntry(BaseModel):
    """One attempted mutation.

    ``diff`` has three sections keyed by collection then entity key:
    ``created`` (full after-image), ``deleted`` (full before-image) and
    ``changed`` (``{field: [before, after]}``).  Failed operations are logged
    with ``ok=False`` and an empty diff (the twin rolls them back atomically).
    """

    seq: int
    time: int
    op: str
    args: dict[str, Any]
    actor: str = "agent"
    ok: bool
    error: str | None = None
    diff: dict[str, dict[str, dict[str, Any]]] = {}


class OpLog(BaseModel):
    entries: list[OpLogEntry] = []

    def append(self, entry: OpLogEntry) -> None:
        self.entries.append(entry)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):  # type: ignore[override]
        return iter(self.entries)


def compute_diff(pre: dict[tuple[str, str], Any], state: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """Diff the pre-images recorded during an operation against the current state."""
    created: dict[str, dict[str, Any]] = {}
    deleted: dict[str, dict[str, Any]] = {}
    changed: dict[str, dict[str, Any]] = {}
    for (kind, key), before in pre.items():
        after = getattr(state, kind).get(key)
        if before is None and after is not None:
            created.setdefault(kind, {})[key] = after.model_dump()
        elif before is not None and after is None:
            deleted.setdefault(kind, {})[key] = before.model_dump()
        elif before is not None and after is not None:
            b, a = before.model_dump(), after.model_dump()
            delta = {f: [b[f], a[f]] for f in b if b[f] != a[f]}
            if delta:
                changed.setdefault(kind, {})[key] = delta
    out = {}
    if created:
        out["created"] = created
    if deleted:
        out["deleted"] = deleted
    if changed:
        out["changed"] = changed
    return out
