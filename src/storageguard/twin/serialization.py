"""JSON persistence helpers for twin states and operation logs."""

from __future__ import annotations

import json
from pathlib import Path

from .oplog import OpLog
from .state import StorageTwin, TwinState


def state_to_json(state: TwinState) -> str:
    return json.dumps(state.model_dump(mode="json"), sort_keys=True)


def state_from_json(text: str) -> TwinState:
    return TwinState.model_validate(json.loads(text))


def save_twin(twin: StorageTwin, path: str | Path) -> None:
    Path(path).write_text(twin.to_json())


def load_twin(path: str | Path) -> StorageTwin:
    return StorageTwin.from_json(Path(path).read_text())


def oplog_to_json(log: OpLog) -> str:
    return log.model_dump_json()
