"""Hard invariants as policy-as-code (rules come from ``configs/policies.yaml``)."""

from __future__ import annotations

from dataclasses import dataclass

from .analyzers import Finding
from .config import GuardConfig


@dataclass(frozen=True)
class Violation:
    invariant: str
    description: str
    on_violation: str  # block | escalate
    finding: Finding

    def to_dict(self) -> dict:
        return {"id": self.invariant, "rule": self.description, "evidence": self.finding.detail}


def check_invariants(findings: list[Finding], cfg: GuardConfig) -> list[Violation]:
    """Match findings against the invariant rules (one violation per invariant, worst evidence first)."""
    out: dict[str, Violation] = {}
    for f in sorted(findings, key=lambda x: -x.severity):
        for rule in cfg.invariants.values():
            if rule.id not in out and rule.violated_by(f.code, f.tier):
                out[rule.id] = Violation(rule.id, rule.description, rule.on_violation, f)
    return [out[k] for k in sorted(out)]
