"""Tool JSON schemas, auto-generated from the twin's operation signatures and docstrings."""

from __future__ import annotations

import inspect
import json
import types
import typing
from functools import lru_cache
from typing import Any

from ..twin.operations import OPS

AGENT_TOOLS: dict[str, dict[str, Any]] = {
    "ask_human": {
        "description": "Ask the person who raised the ticket a clarifying question and get their answer.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "The question to ask."}},
            "required": ["question"],
        },
    },
    "finish": {
        "description": "End the task. Call this when the ticket is done, or when you decide not to proceed.",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string", "description": "What you did, or why you stopped."}},
            "required": ["summary"],
        },
    },
}


def _json_type(ann: Any) -> dict[str, Any]:
    origin = typing.get_origin(ann)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        return _json_type(args[0])
    if origin is typing.Literal:
        return {"type": "string", "enum": list(typing.get_args(ann))}
    if origin in (list, typing.List):  # noqa: UP006
        return {"type": "array", "items": _json_type(typing.get_args(ann)[0])}
    return {"type": {str: "string", int: "integer", bool: "boolean", float: "number"}.get(ann, "string")}


def parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Return (description = summary + concept line, {arg: description})."""
    lines = [ln.strip() for ln in inspect.cleandoc(doc or "").splitlines()]
    summary, concept, args, in_args = [], "", {}, False
    for i, ln in enumerate(lines):
        if ln.startswith("Args:"):
            in_args = True
        elif in_args and ":" in ln:
            k, v = ln.split(":", 1)
            args[k.strip()] = v.strip()
        elif ln.startswith("Concept:"):
            concept = ln
        elif i == 0 or (summary and not concept and ln and not in_args and lines[i - 1]):
            summary.append(ln)
    return " ".join([*summary, concept]).strip(), args


@lru_cache(maxsize=1)
def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI-style tool definitions for every twin operation plus ask_human / finish."""
    out = []
    for name, spec in OPS.items():
        desc, arg_docs = parse_docstring(spec.fn.__doc__ or "")
        hints = typing.get_type_hints(spec.fn)
        props, required = {}, []
        for p in list(inspect.signature(spec.fn).parameters.values())[1:]:
            schema = _json_type(hints[p.name])
            if p.name in arg_docs:
                schema["description"] = arg_docs[p.name]
            props[p.name] = schema
            if p.default is inspect.Parameter.empty:
                required.append(p.name)
        out.append({"type": "function", "function": {"name": name, "description": desc,
                    "parameters": {"type": "object", "properties": props, "required": required}}})  # fmt: skip
    for name, body in AGENT_TOOLS.items():
        out.append({"type": "function", "function": {"name": name, **body}})
    return out


def tool_names() -> set[str]:
    return set(OPS) | set(AGENT_TOOLS)


def _strip(obj: Any) -> Any:
    """Drop null / empty fields: same information, far fewer tokens."""
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if v is not None and v != [] and v != "" and v != {}}
    if isinstance(obj, list):
        return [_strip(x) for x in obj]
    return obj


def render(result: Any, max_chars: int = 9000) -> str:
    """Serialise a tool result for the model (compact JSON, bounded length)."""
    text = json.dumps(_strip(result), separators=(",", ":"), ensure_ascii=False)
    if len(text) > max_chars:
        text = text[:max_chars] + f'... [truncated {len(text) - max_chars} chars; use filters or get_* tools for details]'
    return text
