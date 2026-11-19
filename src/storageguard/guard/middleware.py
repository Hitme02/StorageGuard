"""Middleware layer: every mutating tool call passes through exactly one of these.

``Middleware`` is the common interface for StorageGuard and all baselines.
It returns the payload shown to the agent plus a ``GuardEvent`` for the log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..twin.state import StorageTwin
from .analyzers import Action
from .config import GuardConfig
from .explain import build_feedback
from .policy import ALLOW, BLOCK, ESCALATE, REWRITE, StorageGuard, decision_record


class Approver(Protocol):
    """Whoever answers escalations (the simulated human in experiments)."""

    def review(self, twin: StorageTwin, steps: list[Action], original: Action) -> bool: ...


@dataclass
class GuardEvent:
    """What the middleware decided about one mutating call."""

    action: dict[str, Any]
    decision: str  # ALLOW | REWRITE | ESCALATE | BLOCK
    status: str  # executed | rewritten | blocked | pending_approval | denied_by_human | error
    executed: list[dict[str, Any]] = field(default_factory=list)
    human: str | None = None  # approved | denied | unavailable
    latency_ms: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    extra_llm_calls: int = 0
    extra_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class Middleware:
    """Base class.  ``name`` identifies the configuration in logs."""

    name = "base"
    prompt_variant = "default"  # which system prompt the agent gets

    def __init__(self, approver: Approver | None = None) -> None:
        self.approver = approver

    def handle(self, twin: StorageTwin, action: Action) -> tuple[dict[str, Any], GuardEvent]:  # pragma: no cover
        raise NotImplementedError

    # ---- shared helpers ----
    @staticmethod
    def _run(twin: StorageTwin, steps: list[Action]) -> tuple[list[dict[str, Any]], bool]:
        results, ok = [], True
        for s in steps:
            r = twin.execute(s.tool, s.args)
            results.append(r)
            if not r.get("ok"):
                ok = False
                break
        return results, ok

    def _ask(self, twin: StorageTwin, steps: list[Action], original: Action) -> str:
        if self.approver is None:
            return "unavailable"
        return "approved" if self.approver.review(twin, steps, original) else "denied"


class NoGuard(Middleware):
    """B0: every call goes straight to the array."""

    name = "noguard"

    def handle(self, twin: StorageTwin, action: Action) -> tuple[dict[str, Any], GuardEvent]:
        res = twin.execute(action.tool, action.args)
        status = "executed" if res.get("ok") else "error"
        return res, GuardEvent(action.to_dict(), ALLOW, status, [action.to_dict()] if res.get("ok") else [])


class StorageGuardMiddleware(Middleware):
    """B4 (and ablations A1-A5): the full StorageGuard pipeline."""

    name = "storageguard"

    def __init__(self, cfg: GuardConfig | None = None, approver: Approver | None = None) -> None:
        super().__init__(approver)
        self.cfg = cfg or GuardConfig()
        self.guard = StorageGuard(self.cfg)

    def handle(self, twin: StorageTwin, action: Action) -> tuple[dict[str, Any], GuardEvent]:
        t0 = time.perf_counter()
        d = self.guard.decide(twin, action)
        latency = (time.perf_counter() - t0) * 1000
        ev = GuardEvent(action.to_dict(), d.kind, "", latency_ms=round(latency, 3), detail=decision_record(d))

        if d.kind == ALLOW:
            res = twin.execute(action.tool, action.args)
            ev.status = "executed" if res.get("ok") else "error"
            ev.executed = [action.to_dict()] if res.get("ok") else []
            return res, ev
        if d.kind == REWRITE:
            results, ok = self._run(twin, d.execute)
            ev.status = "rewritten" if ok else "error"
            ev.executed = [s.to_dict() for s, _ in zip(d.execute, results, strict=False)]
            return build_feedback(d, ev.status, self.cfg, result=results[-1]), ev
        if d.kind == ESCALATE:
            verdict = self._ask(twin, d.execute, action)
            ev.human = verdict
            if verdict == "approved":
                results, ok = self._run(twin, d.execute)
                ev.status = "executed" if ok else "error"
                ev.executed = [s.to_dict() for s, _ in zip(d.execute, results, strict=False)]
                return build_feedback(d, ev.status, self.cfg, result=results[-1],
                                      note="escalated to a human storage administrator, who approved it"), ev  # fmt: skip
            ev.status = "denied_by_human" if verdict == "denied" else "pending_approval"
            note = ("a human storage administrator reviewed and denied this action" if verdict == "denied"
                    else "this action needs human approval and no approver is available; it was not executed")  # fmt: skip
            return build_feedback(d, ev.status, self.cfg, note=note), ev
        assert d.kind == BLOCK
        ev.status = "blocked"
        return build_feedback(d, "blocked", self.cfg), ev
