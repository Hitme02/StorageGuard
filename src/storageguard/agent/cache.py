"""Disk-backed LLM response cache keyed on hash(model, messages, tools, params).

Reruns of an experiment are free and bit-for-bit reproducible: the same prompt
always yields the stored completion.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import diskcache

from ..paths import repo_root


def cache_key(model: str, messages: list[dict], tools: list[dict] | None, params: dict[str, Any]) -> str:
    blob = json.dumps({"model": model, "messages": messages, "tools": tools, "params": params},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)  # fmt: skip
    return hashlib.sha256(blob.encode()).hexdigest()


class ResponseCache:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else repo_root() / ".cache" / "llm"
        self.path.mkdir(parents=True, exist_ok=True)
        self._db = diskcache.Cache(str(self.path), size_limit=8 * 2**30)

    def get(self, key: str) -> dict | None:
        return self._db.get(key)

    def put(self, key: str, value: dict) -> None:
        self._db.set(key, value)

    def __len__(self) -> int:
        return len(self._db)

    def close(self) -> None:
        self._db.close()
