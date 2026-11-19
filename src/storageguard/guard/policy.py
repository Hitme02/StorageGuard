"""StorageGuard decision policy: dry run -> impact analysis -> ALLOW / REWRITE / ESCALATE / BLOCK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..graph.builder import build_graph
from ..graph.closure import Impact, closure, one_hop_neighbourhood
from ..twin.operations import ARG_COLLECTION
from ..twin.public import PublicState, capture_public
from ..twin.state import StorageTwin
from .analyzers import Action, Finding, StepContext, analyse_step
from .config import GuardConfig
from .invariants import Violation, check_invariants
from .reversibility import classify
from .rewrites import Rewrite, candidates
from .risk import analyzer_severities, risk_score

ALLOW, REWRITE, ESCALATE, BLOCK = "ALLOW", "REWRITE", "ESCALATE", "BLOCK"


@dataclass
class Assessment:
    """Everything the guard learned from dry-running an action sequence."""

    steps: list[Action]
    findings: list[Finding] = field(default_factory=list)
    rclass: int = 0
    risk: float = 0.0
    violations: list[Violation] = field(default_factory=list)
    impacted: list[Impact] = field(default_factory=list)
    error: str | None = None

    @property
    def hard_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.on_violation == "block"]

    @property
    def soft_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.on_violation != "block"]

    @property
    def severities(self) -> dict[str, float]:
        return analyzer_severities(self.findings)


@dataclass
class Decision:
    kind: str
    action: Action
    assessment: Assessment
    execute: list[Action]  # what runs on ALLOW / REWRITE / an approved ESCALATE
    rewrite: Rewrite | None = None
    rewrite_assessment: Assessment | None = None
    before: PublicState | None = None


def action_starts(action: Action, ps: PublicState) -> list[tuple[str, str]]:
    """Entities an action touches and how (closure start modes)."""
    tool, args = action.tool, action.args
    out: list[tuple[str, str]] = []
    mode_by_tool = {
        "delete_volume": "access" if args.get("to_recycle_bin") else "destroyed",
        "purge_recycle_bin": "destroyed",
        "delete_snapshot": "destroyed",
        "delete_backup": "destroyed",
        "delete_zone": "destroyed",
        "remove_zone_member": "affected",
        "break_replication": "destroyed",
        "pause_replication": "affected",
        "unmap_volume": "access",
        "remove_hot_spare": "affected",
    }
    mode = mode_by_tool.get(tool, "affected")
    for arg, val in args.items():
        if arg in ARG_COLLECTION and isinstance(val, str) and ps.kind_of(val):
            kind = ps.kind_of(val)
            m = mode
            if kind == "hosts":
                m = "touch"
            elif kind == "pools":
                m = "touch"
            elif kind == "volumes" and tool == "restore_snapshot":
                m = "affected"
            elif kind == "snapshots" and tool == "restore_snapshot":
                m = "touch"
            out.append((val, m))
    if tool == "purge_recycle_bin" and not args.get("volume_id"):
        out += [(v["id"], "destroyed") for v in ps.volumes.values() if v.get("in_recycle_bin")]
    if tool in ("create_zone", "add_zone_member", "remove_zone_member"):
        wwpns = list(args.get("members") or []) + ([args["wwpn"]] if args.get("wwpn") else [])
        for h in ps.hosts.values():
            if set(h.get("initiators", [])) & set(wwpns):
                out.append((h["id"], "access"))
        for a in ps.arrays.values():
            if set(a.get("target_ports", [])) & set(wwpns):
                out.append((a["id"], "touch"))
    return out


class StorageGuard:
    """Blast-radius-aware guard.  Stateless between calls; safe to share across episodes."""

    def __init__(self, cfg: GuardConfig | None = None) -> None:
        self.cfg = cfg or GuardConfig()

    # ---------------- assessment ----------------
    def assess(self, twin: StorageTwin, steps: list[Action], before: PublicState | None = None) -> Assessment:
        """Dry-run ``steps`` on a clone of the twin and score the combined impact."""
        cfg = self.cfg
        before_full = before or capture_public(twin)
        trial = twin.clone()
        out = Assessment(steps=steps)
        cur = before_full
        graph0 = build_graph(before_full)
        for step in steps:
            res = trial.execute(step.tool, step.args, actor="guard-dry-run")
            if not res.get("ok"):
                out.error = res.get("error", "failed")
                break
            after = capture_public(trial)
            b, a = cur, after
            if cfg.one_hop_only:
                g = graph0 if cur is before_full else build_graph(cur)
                keep = one_hop_neighbourhood(g, [e for e, _ in action_starts(step, cur)])
                for kind in PublicState.ENTITY_KINDS:  # entities created by this step are visible too
                    keep.update(k for k in getattr(after, kind) if k not in getattr(cur, kind))
                b, a = cur.restrict(keep), after.restrict(keep)
            found = analyse_step(StepContext(step, b, a, res, cfg))
            out.findings += found
            out.rclass = max(out.rclass, classify(step, found, res))
            cur = after
        out.risk = risk_score(out.findings, out.rclass, cfg)
        out.violations = check_invariants(out.findings, cfg)
        out.impacted = closure(graph0, action_starts(steps[0], before_full), max_hops=1 if cfg.one_hop_only else None)
        return out

    # ---------------- decision ----------------
    def decide(self, twin: StorageTwin, action: Action) -> Decision:
        cfg = self.cfg
        before = capture_public(twin)
        a = self.assess(twin, [action], before)
        if a.error is not None:  # the twin will reject it anyway; nothing to guard
            return Decision(ALLOW, action, a, [action], before=before)

        rewrites = candidates(action, before) if cfg.enable_rewrites else []
        if a.violations or a.risk >= cfg.tau_allow:
            for rw in rewrites:
                ra = self.assess(twin, rw.steps[:-1] if rw.escalate else rw.steps, before)
                if ra.error is not None or ra.violations:
                    continue  # a rewrite must itself be clean: no invariant hit and risk below tau_allow
                if rw.escalate:  # preparatory steps are safe; the risky step still needs a human
                    if ra.risk < cfg.tau_allow:
                        return Decision(ESCALATE, action, a, rw.steps, rw, ra, before)
                elif ra.risk < cfg.tau_allow:
                    return Decision(REWRITE, action, a, rw.steps, rw, ra, before)
        if a.hard_violations or a.risk >= cfg.tau_block:
            return Decision(BLOCK, action, a, [], before=before)
        if a.soft_violations or a.risk >= cfg.tau_allow:
            return Decision(ESCALATE, action, a, [action], before=before)
        return Decision(ALLOW, action, a, [action], before=before)


def decision_record(d: Decision) -> dict[str, Any]:
    """Compact, JSON-serialisable log record of a decision (for trajectories and metrics)."""
    a = d.assessment
    return {
        "decision": d.kind,
        "risk": a.risk,
        "rclass": a.rclass,
        "severities": {k: round(v, 3) for k, v in a.severities.items() if v},
        "findings": [f.to_dict() for f in sorted(a.findings, key=lambda f: -f.severity)],
        "invariants": [v.invariant for v in a.violations],
        "impacted_count": len(a.impacted),
        "rewrite": d.rewrite.to_dict() if d.rewrite else None,
        "rewrite_risk": d.rewrite_assessment.risk if d.rewrite_assessment else None,
    }
