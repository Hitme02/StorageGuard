"""The agent loop: ticket -> LLM tool calls -> middleware -> twin, with a JSONL trajectory."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..guard.analyzers import Action
from ..guard.middleware import GuardEvent, Middleware
from ..twin.operations import OPS, validate_args
from ..twin.public import iso
from ..twin.state import StorageTwin
from .llm_client import LLMClient, ToolCall, salvage_tool_calls
from .tools import render, tool_names, tool_schemas

PROMPT_DIR = Path(__file__).parent / "prompts"
MAX_STEPS = 30
REPAIR_MESSAGE = (
    "Your last message did not contain a valid tool call. Respond with exactly one tool call "
    "(valid tool name, JSON arguments matching the schema). Call finish when you are done."
)
ELIDED = "[older tool output elided to save context; call the tool again if you need it]"


def tool_call_id(step: int, i: int) -> str:
    """Deterministic 9-character id (Mistral requires exactly 9 alphanumerics): same transcript on every backend.

    ``call<step><i>`` for the first 100 calls of a reply; a degenerate reply with more calls
    (seen once: Ministral repeating a call 100+ times) continues as ``c<step><i:05d>`` so ids stay unique.
    """
    if i < 100:
        return f"call{step % 1000:03d}{i:02d}"
    return f"c{step % 1000:03d}{i % 100000:05d}"


def system_prompt(variant: str = "default") -> str:
    text = (PROMPT_DIR / "system.md").read_text()
    if variant == "safety":
        text += (PROMPT_DIR / "safety_rules.md").read_text()
    return text


@dataclass
class Trajectory:
    """Everything that happened in one episode."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    guard_events: list[GuardEvent] = field(default_factory=list)
    steps: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_latency_s: float = 0.0
    finished: bool = False
    termination: str = "max_steps"
    summary: str = ""
    asked: int = 0
    cached_calls: int = 0


class _Log:
    def __init__(self, path: Path | None) -> None:
        self.fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = path.open("w")

    def write(self, traj: Trajectory, kind: str, **data: Any) -> None:
        rec = {"type": kind, **data}
        traj.events.append(rec)
        if self.fh:
            self.fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def close(self) -> None:
        if self.fh:
            self.fh.close()


def _compact(messages: list[dict[str, Any]], budget_chars: int) -> list[dict[str, Any]]:
    """Elide the oldest large tool outputs once the transcript exceeds the context budget."""
    total = sum(len(m.get("content") or "") for m in messages)
    if total <= budget_chars:
        return messages
    out = [dict(m) for m in messages]
    for m in out[:-4]:
        if total <= budget_chars:
            break
        if m["role"] == "tool" and len(m.get("content") or "") > 400:
            total -= len(m["content"]) - len(ELIDED)
            m["content"] = ELIDED
    return out


def run_episode(
    instruction: str,
    twin: StorageTwin,
    client: LLMClient,
    middleware: Middleware,
    human: Any = None,
    max_steps: int = MAX_STEPS,
    log_path: Path | None = None,
    context_chars: int = 36000,
    llm_params: dict[str, Any] | None = None,
    on_event: Any = None,
) -> Trajectory:
    """Run one episode to completion (``finish`` or the step limit)."""
    traj, log = Trajectory(), _Log(log_path)
    tools, names = tool_schemas(), tool_names()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(middleware.prompt_variant)},
        {"role": "user", "content": f"Current time: {iso(twin.now)}\n\nTicket:\n{instruction}"},
    ]
    for m in messages:
        log.write(traj, "message", **m)
    repairs_in_a_row = 0

    def emit(kind: str, **data: Any) -> None:
        log.write(traj, kind, **data)
        if on_event:
            on_event(kind, data)

    while traj.steps < max_steps and not traj.finished:
        traj.steps += 1
        resp = client.chat(_compact(messages, context_chars), tools, **(llm_params or {}))
        traj.prompt_tokens += resp.prompt_tokens
        traj.completion_tokens += resp.completion_tokens
        traj.llm_latency_s += resp.latency_s
        traj.cached_calls += int(resp.cached)
        calls = resp.tool_calls
        salvaged = False
        if not calls:
            calls = salvage_tool_calls(resp.content, names)
            salvaged = bool(calls)
        emit("llm", step=traj.steps, content=resp.content, tool_calls=[c.__dict__ for c in calls],
             prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens, latency_s=resp.latency_s, cached=resp.cached)  # fmt: skip
        if not calls:
            traj.errors += 1
            repairs_in_a_row += 1
            if repairs_in_a_row >= 2:
                traj.termination = "no_tool_call"
                break
            messages.append({"role": "assistant", "content": resp.content or "(no tool call)"})
            messages.append({"role": "user", "content": REPAIR_MESSAGE})
            emit("repair", step=traj.steps)
            continue
        repairs_in_a_row = 0
        for i, c in enumerate(calls):
            c.id = tool_call_id(traj.steps, i)
        # A salvaged call is re-sent in the tool channel only: repeating its raw text as content would duplicate it,
        # and Mistral's chat format rejects an assistant message that has both text and tool calls.
        messages.append({
            "role": "assistant", "content": "" if salvaged else (resp.content or ""),
            "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments if c.arguments is not None else {})}} for c in calls],
        })  # fmt: skip
        for call in calls:
            payload = _dispatch(call, twin, middleware, human, traj, emit)
            text = render(payload)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
            emit("tool_result", step=traj.steps, tool=call.name, content=text)
            if traj.finished:
                break
    log.write(traj, "end", termination=traj.termination, steps=traj.steps, errors=traj.errors)
    log.close()
    traj.messages = messages
    return traj


def _dispatch(call: ToolCall, twin: StorageTwin, middleware: Middleware, human: Any, traj: Trajectory, emit: Any) -> dict[str, Any]:
    name, args = call.name, call.arguments
    if args is None:
        traj.errors += 1
        return {"ok": False, "error": f"arguments for {name} were not valid JSON; send a JSON object"}
    if name == "finish":
        traj.finished, traj.termination, traj.summary = True, "finish", str(args.get("summary", ""))
        return {"ok": True}
    if name == "ask_human":
        traj.asked += 1
        answer = human.answer(str(args.get("question", ""))) if human is not None else "No one is available to answer."
        return {"answer": answer}
    spec = OPS.get(name)
    if spec is None:
        traj.errors += 1
        return {"ok": False, "error": f"unknown tool '{name}'"}
    args = twin.resolve_args(name, args)
    try:
        coerced = validate_args(spec, args)
    except ValueError as exc:
        traj.errors += 1
        return {"ok": False, "error": f"invalid arguments for {name}: {exc}"}
    # The twin executes the validated values ("false" -> False, "100" -> 100), so every middleware must judge
    # exactly those. Same keys as the model sent: well-typed calls are passed through unchanged.
    args = {k: coerced[k] for k in args}
    if not spec.mutating:
        return twin.execute(name, args)
    payload, event = middleware.handle(twin, Action(name, args))
    traj.guard_events.append(event)
    emit("guard", step=traj.steps, **event.to_dict())
    return payload
