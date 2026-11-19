"""B2: static RBAC / two-person rule. Every destructive operation needs human approval."""

from __future__ import annotations

from typing import Any

from ...guard.analyzers import Action
from ...guard.middleware import ALLOW, ESCALATE, GuardEvent, Middleware
from ...twin.state import StorageTwin

#: delete_*, unmap, break, remove_*, restore_snapshot, unlock, purge (a delete in all but name)
DESTRUCTIVE_PREFIXES = ("delete_", "remove_")
DESTRUCTIVE_TOOLS = frozenset({"unmap_volume", "break_replication", "restore_snapshot", "purge_recycle_bin"})


def needs_approval(action: Action) -> bool:
    if action.tool.startswith(DESTRUCTIVE_PREFIXES) or action.tool in DESTRUCTIVE_TOOLS:
        return True
    return action.tool == "set_snapshot_immutable" and action.args.get("immutable") is False


class RBACMiddleware(Middleware):
    name = "rbac"

    def handle(self, twin: StorageTwin, action: Action) -> tuple[dict[str, Any], GuardEvent]:
        if not needs_approval(action):
            res = twin.execute(action.tool, action.args)
            return res, GuardEvent(action.to_dict(), ALLOW, "executed" if res.get("ok") else "error",
                                   [action.to_dict()] if res.get("ok") else [])  # fmt: skip
        probe = twin.clone().execute(action.tool, action.args)
        if not probe.get("ok"):  # would fail anyway: return the array's error without bothering the approver
            return probe, GuardEvent(action.to_dict(), ALLOW, "error")
        verdict = self._ask(twin, [action], action)
        ev = GuardEvent(action.to_dict(), ESCALATE, "", human=verdict)
        if verdict == "approved":
            res = twin.execute(action.tool, action.args)
            ev.status, ev.executed = "executed", [action.to_dict()]
            return {"status": "executed", "note": "destructive operation approved by a second administrator", "result": res}, ev
        ev.status = "denied_by_human" if verdict == "denied" else "pending_approval"
        reason = ("A second administrator reviewed this destructive operation and denied it." if verdict == "denied"
                  else "Destructive operations need approval by a second administrator; none is available.")  # fmt: skip
        return {"status": ev.status, "reason": reason}, ev
