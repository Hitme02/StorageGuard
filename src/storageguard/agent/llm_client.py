"""LLM clients: an OpenAI-compatible client (Ollama, vLLM, LM Studio, hosted APIs) and a scripted one."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from ..bench.schema import PlanStep
from ..paths import config_dir
from .cache import ResponseCache, cache_key


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None  # None when the JSON could not be parsed
    raw_arguments: str = ""


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"content": self.content, "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "latency_s": self.latency_s,
                "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments, "raw_arguments": c.raw_arguments}
                               for c in self.tool_calls]}  # fmt: skip

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LLMResponse:
        return cls(d.get("content", ""), [ToolCall(**c) for c in d.get("tool_calls", [])], d.get("prompt_tokens", 0),
                   d.get("completion_tokens", 0), d.get("latency_s", 0.0))  # fmt: skip


class LLMClient(Protocol):
    model: str

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **params: Any) -> LLMResponse: ...


def _parse_args(raw: str | dict | None) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if raw is None or raw == "":
        return {}
    try:
        val = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return val if isinstance(val, dict) else None


_DECODER = json.JSONDecoder()


def _as_call(obj: Any, names: set[str]) -> ToolCall | None:
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function")
    if isinstance(fn, dict):  # OpenAI-style {"function": {"name": ..., "arguments": ...}}
        obj = fn
        fn = None
    name = obj.get("name") or obj.get("tool") or fn
    args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
    parsed = _parse_args(args)
    if isinstance(name, str) and name in names and isinstance(parsed, dict):
        return ToolCall("salvaged", name, parsed, args if isinstance(args, str) else json.dumps(args))
    return None


def salvage_tool_calls(content: str, names: set[str]) -> list[ToolCall]:
    """Recover tool calls a model printed as text instead of using the tool channel.

    Handles bare JSON, ``<tool_call>`` tags (also doubled or unbalanced ones), the
    ``"function"`` key some models use instead of ``"name"``, and several calls in one
    reply.  Every JSON object in the text is tried; objects that are not a call to a
    known tool are ignored.
    """
    out: list[ToolCall] = []
    text, i = content or "", 0
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = _DECODER.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        call = _as_call(obj, names)
        if call is not None:
            out.append(call)
            i = end
        else:
            i += 1
    return out


def salvage_tool_call(content: str, names: set[str]) -> ToolCall | None:
    calls = salvage_tool_calls(content, names)
    return calls[0] if calls else None


def split_tool_call_turns(messages: list[dict]) -> list[dict]:
    """Re-serialise every assistant message with several tool calls as consecutive single-call turns.

    ``[assistant(c1, c2), tool(r1), tool(r2)]`` becomes ``[assistant(c1), tool(r1), assistant(c2), tool(r2)]``.
    Same calls, same results, same order; only the framing changes. Needed for chat templates that accept one tool
    call per assistant message (Llama 3.1 rejects the request otherwise: DECISIONS.md, C15). Transcripts without a
    multi-call message are returned unchanged.
    """
    if not any(m.get("role") == "assistant" and len(m.get("tool_calls") or []) > 1 for m in messages):
        return messages
    out: list[dict] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        calls = m.get("tool_calls") or []
        if m.get("role") != "assistant" or len(calls) <= 1:
            out.append(m)
            i += 1
            continue
        j = i + 1
        results: dict[str, dict] = {}
        while j < len(messages) and messages[j].get("role") == "tool":
            results[messages[j].get("tool_call_id", "")] = messages[j]
            j += 1
        for k, c in enumerate(calls):
            out.append({**m, "content": (m.get("content") or "") if k == 0 else "", "tool_calls": [c]})
            if c.get("id") in results:
                out.append(results.pop(c["id"]))
        out.extend(results.values())  # a result whose id matched no call (not produced by the loop) keeps its place
        i = j
    return out


class OpenAICompatibleClient:
    """Chat-completions client for any OpenAI-compatible endpoint, with a response cache."""

    def __init__(self, model: str, base_url: str = "http://localhost:11434/v1", api_key_env: str | None = None,
                 cache: ResponseCache | None = None, temperature: float = 0.0, max_tokens: int = 1024,
                 timeout: float = 600.0, extra: dict[str, Any] | None = None, single_tool_call_turns: bool = False) -> None:  # fmt: skip
        from openai import OpenAI

        key = os.environ.get(api_key_env, "") if api_key_env else ""
        self.model = model
        self.base_url = base_url
        self._client = OpenAI(base_url=base_url, api_key=key or "not-needed", timeout=timeout, max_retries=2)
        self.cache = cache
        self.defaults = {"temperature": temperature, "max_tokens": max_tokens, **(extra or {})}
        self.single_tool_call_turns = single_tool_call_turns

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **params: Any) -> LLMResponse:
        if self.single_tool_call_turns:  # the cache key below is computed on exactly what is sent
            messages = split_tool_call_turns(messages)
        p = {**self.defaults, **params}
        key = cache_key(self.model, messages, tools, p)
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                resp = LLMResponse.from_dict(hit)
                resp.cached = True
                return resp
        api = {k: v for k, v in p.items() if not k.startswith("sg_")}  # sg_* only vary the cache key
        t0 = time.perf_counter()
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, **api}
        if tools:
            kwargs["tools"] = tools
        out = self._client.chat.completions.create(**kwargs)
        msg = out.choices[0].message
        calls = [ToolCall(c.id or f"call_{i}", c.function.name, _parse_args(c.function.arguments), c.function.arguments or "")
                 for i, c in enumerate(msg.tool_calls or [])]  # fmt: skip
        usage = out.usage
        resp = LLMResponse(msg.content or "", calls, getattr(usage, "prompt_tokens", 0) or 0,
                           getattr(usage, "completion_tokens", 0) or 0, round(time.perf_counter() - t0, 3))  # fmt: skip
        if self.cache is not None:
            self.cache.put(key, resp.to_dict())
        return resp


class ScriptedClient:
    """Replays a fixed plan: one tool call per turn, then ``finish``. No network, no randomness."""

    def __init__(self, plan: list[PlanStep], model: str = "scripted") -> None:
        self.model = model
        self._plan = list(plan)
        self._i = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **params: Any) -> LLMResponse:
        if self._i < len(self._plan):
            step = self._plan[self._i]
            self._i += 1
            return LLMResponse(tool_calls=[ToolCall(f"s{self._i}", step.tool, dict(step.args), json.dumps(step.args))])
        return LLMResponse(tool_calls=[ToolCall("fin", "finish", {"summary": "plan complete"}, "{}")])


# --------------------------------------------------------------------------- #
# model registry (configs/models.yaml)
# --------------------------------------------------------------------------- #


@dataclass
class ModelSpec:
    name: str
    model: str
    base_url: str = "http://localhost:11434/v1"
    api_key_env: str | None = None
    backend: str = "ollama"
    concurrency: int = 1
    context_chars: int = 36000
    max_tokens: int = 1024
    extra: dict[str, Any] = field(default_factory=dict)
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0
    single_tool_call_turns: bool = False  # the chat template takes one tool call per assistant message (C15)


def load_models(path: str | Path | None = None) -> dict[str, ModelSpec]:
    """Model registry: ``path``, else ``$SG_MODELS_FILE``, else ``configs/models.yaml``."""
    path = path or os.environ.get("SG_MODELS_FILE") or config_dir() / "models.yaml"
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return {name: ModelSpec(name=name, **body) for name, body in (raw.get("models") or {}).items()}


def make_client(spec: ModelSpec, cache: ResponseCache | None = None) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(spec.model, spec.base_url, spec.api_key_env, cache, max_tokens=spec.max_tokens, extra=spec.extra,
                                  single_tool_call_turns=spec.single_tool_call_turns)  # fmt: skip
