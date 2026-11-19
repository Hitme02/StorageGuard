"""Configuration registry: baselines B0-B4 and ablations A1-A5.

A config spec is ``name[@key=value,...]``; keys: ``human`` (oracle | none |
rubber_stamp), ``tau_allow``, ``tau_block``.  Examples: ``storageguard``,
``rbac@human=none``, ``storageguard@tau_allow=0.1,tau_block=0.5``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...guard.config import GuardConfig
from ...guard.middleware import Middleware, NoGuard, StorageGuardMiddleware
from .llm_judge import JudgeMiddleware
from .rbac import RBACMiddleware


class PromptOnly(NoGuard):
    """B1: no guard, but the system prompt carries explicit safety rules."""

    name = "prompt_only"
    prompt_variant = "safety"


ABLATIONS: dict[str, dict[str, Any]] = {
    "a1_one_hop": {"one_hop_only": True},
    "a2_no_reversibility": {"use_reversibility": False},
    "a3_no_rewrites": {"enable_rewrites": False},
    "a4_no_feedback": {"structured_feedback": False},
    "p1_pit_check": {"pit_check": True},  # post-hoc, found by the physical replay (DECISIONS.md section 11)
}
LABELS = {
    "noguard": "B0 No guard", "prompt_only": "B1 Prompt rules", "rbac": "B2 RBAC approval", "llm_judge": "B3 LLM judge",
    "storageguard": "B4 StorageGuard", "a1_one_hop": "A1 one-hop", "a2_no_reversibility": "A2 no reversibility",
    "a3_no_rewrites": "A3 no rewrites", "a4_no_feedback": "A4 no feedback",
    "p1_pit_check": "B4+PIT B4 + newest-point-in-time-copy check (post-hoc)",
}  # fmt: skip
MAIN_CONFIGS = ["noguard", "prompt_only", "rbac", "llm_judge", "storageguard"]


@dataclass(frozen=True)
class ConfigSpec:
    spec: str
    base: str
    human: str = "oracle"
    overrides: dict[str, float] = field(default_factory=dict)

    @property
    def uses_judge(self) -> bool:
        return self.base == "llm_judge"

    @property
    def label(self) -> str:
        extra = self.spec.split("@", 1)[1] if "@" in self.spec else ""
        return LABELS.get(self.base, self.base) + (f" ({extra})" if extra else "")


def parse_config(spec: str) -> ConfigSpec:
    base, _, tail = spec.partition("@")
    if base not in LABELS:
        raise ValueError(f"unknown config '{base}'; choose from {sorted(LABELS)}")
    human, overrides = "oracle", {}
    for part in filter(None, tail.split(",")):
        k, v = part.split("=", 1)
        if k == "human":
            human = v
        elif k in ("tau_allow", "tau_block"):
            overrides[k] = float(v)
        else:
            raise ValueError(f"unknown config key '{k}' in '{spec}'")
    return ConfigSpec(spec, base, human, overrides)


def make_middleware(spec: str | ConfigSpec, human: Any = None, judge: Any = None, guard_cfg: GuardConfig | None = None,
                    llm_params: dict[str, Any] | None = None) -> Middleware:  # fmt: skip
    """Build the middleware for a config.  ``human`` is the approver (None when human=none)."""
    cs = parse_config(spec) if isinstance(spec, str) else spec
    approver = None if cs.human == "none" else human
    if cs.base == "noguard":
        return NoGuard()
    if cs.base == "prompt_only":
        return PromptOnly()
    if cs.base == "rbac":
        return RBACMiddleware(approver)
    if cs.base == "llm_judge":
        if judge is None:
            raise ValueError("llm_judge needs a judge LLM client")
        return JudgeMiddleware(judge, approver, llm_params)
    cfg = (guard_cfg or GuardConfig.load()).with_overrides(**ABLATIONS.get(cs.base, {}), **cs.overrides)
    mw = StorageGuardMiddleware(cfg, approver)
    mw.name = cs.base
    return mw
