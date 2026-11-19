"""B3: LLM-as-judge. One extra LLM call per mutating action, with one-hop graph context."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...agent.llm_client import LLMClient
from ...agent.tools import render
from ...graph.builder import build_graph
from ...graph.closure import one_hop_neighbourhood
from ...guard.analyzers import Action
from ...guard.middleware import ALLOW, BLOCK, ESCALATE, GuardEvent, Middleware
from ...guard.policy import action_starts
from ...twin.public import capture_public
from ...twin.state import StorageTwin

JUDGE_PROMPT = (Path(__file__).parents[2] / "agent" / "prompts" / "judge.md").read_text()
_VERDICT = re.compile(r'"verdict"\s*:\s*"(ALLOW|ESCALATE|BLOCK)"', re.I)


def judge_context(twin: StorageTwin, action: Action, max_chars: int = 7000) -> str:
    """The action, its target entities and their one-hop neighbours (public JSON only)."""
    ps = capture_public(twin)
    targets = [e for e, _ in action_starts(action, ps)]
    keep = one_hop_neighbourhood(build_graph(ps), targets)
    ents = {"targets": [ps.get(t) for t in targets if ps.get(t)],
            "neighbours": [ps.get(n) for n in sorted(keep - set(targets)) if ps.get(n)]}  # fmt: skip
    return render({"proposed_action": action.to_dict(), **ents}, max_chars)


def parse_verdict(text: str) -> tuple[str, str]:
    m = _VERDICT.search(text or "")
    if not m:
        return ESCALATE, "judge output could not be parsed"
    reason = ""
    try:
        reason = str(json.loads(re.search(r"\{.*\}", text, re.S).group(0)).get("reason", ""))  # type: ignore[union-attr]
    except (json.JSONDecodeError, AttributeError):
        pass
    return m.group(1).upper(), reason


class JudgeMiddleware(Middleware):
    name = "llm_judge"

    def __init__(self, judge: LLMClient, approver: Any = None, llm_params: dict[str, Any] | None = None) -> None:
        super().__init__(approver)
        self.judge = judge
        self.llm_params = llm_params or {}

    def handle(self, twin: StorageTwin, action: Action) -> tuple[dict[str, Any], GuardEvent]:
        probe = twin.clone().execute(action.tool, action.args)
        if not probe.get("ok"):
            return probe, GuardEvent(action.to_dict(), ALLOW, "error")
        msgs = [{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": judge_context(twin, action)}]
        resp = self.judge.chat(msgs, None, **self.llm_params)
        verdict, reason = parse_verdict(resp.content)
        ev = GuardEvent(action.to_dict(), verdict, "", latency_ms=round(resp.latency_s * 1000, 1), detail={"reason": reason},
                        extra_llm_calls=1, extra_tokens=resp.prompt_tokens + resp.completion_tokens)  # fmt: skip
        if verdict == BLOCK:
            ev.status = "blocked"
            return {"status": "blocked", "reason": reason or "blocked by change-control review"}, ev
        if verdict == ESCALATE:
            ev.human = self._ask(twin, [action], action)
            if ev.human != "approved":
                ev.status = "denied_by_human" if ev.human == "denied" else "pending_approval"
                return {"status": ev.status, "reason": reason or "needs human approval"}, ev
        res = twin.execute(action.tool, action.args)
        ev.status, ev.executed = "executed", [action.to_dict()]
        return res, ev
