"""Replay recorded episodes on real files, and compare the baselines by what was really lost.

Every episode of the study was logged step by step (``<task>__r<run>.jsonl``).  The sandbox
changes nothing the agent can observe, so feeding the recorded model replies back in order
re-creates each episode on real files.  This is checked, not assumed: every tool result the
agent sees and every middleware decision must equal the recorded one, and so must the
oracle's verdict; an episode that diverges is flagged and excluded.  StorageGuard, RBAC and
the simulated approver run live; the agent's replies and the LLM judge's verdicts are model
outputs and are replayed (exactly what a rerun through the response cache would return).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agent.llm_client import LLMResponse, ToolCall
from ..bench.schema import Task
from ..bench.splits import load_bench, task_twin
from ..guard.config import GuardConfig
from ..paths import results_dir
from ..physical.measure import DrillResult
from ..physical.session import reference
from .baselines import parse_config
from .runner import run_one

GUARD_KEYS = ("action", "decision", "status", "executed", "human")
ROW_KEYS = ("unsafe", "harm_codes", "goal_met", "success", "finished", "termination", "steps", "n_mutating",
            "n_escalations", "n_stops")  # fmt: skip


@dataclass
class PhysicalOptions:
    """How ``run_one`` executes on real files: where sandboxes go, and whether to keep one."""

    base_dir: str | None = None
    keep: bool = False
    _refs: dict[tuple[str, str], DrillResult] = field(default_factory=dict)

    def reference_for(self, task: Task, executed: list[tuple[str, dict]]) -> DrillResult:
        """Reference drills for the authorised subset of the calls an episode executed (cached)."""
        calls = [(t, a) for t, a in executed if task.allowed_changes.permits(t, a)]
        key = (task.id, json.dumps(calls, sort_keys=True, default=str))
        if key not in self._refs:
            self._refs[key] = reference(task_twin(task), task.allowed_changes, calls, self.base_dir)
        return self._refs[key]


class ReplayDivergence(RuntimeError):
    """The live episode asked for something the recorded one never did."""


@dataclass
class Trace:
    llm: list[dict[str, Any]] = field(default_factory=list)
    guard: list[dict[str, Any]] = field(default_factory=list)
    results: list[str] = field(default_factory=list)


def load_trace(path: Path) -> Trace:
    tr = Trace()
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        if rec["type"] == "llm":
            tr.llm.append(rec)
        elif rec["type"] == "guard":
            tr.guard.append(rec)
        elif rec["type"] == "tool_result":
            tr.results.append(rec["content"])
    return tr


class ReplayClient:
    """Returns the recorded replies of the agent model, in order."""

    def __init__(self, trace: Trace, model: str) -> None:
        self.model, self._llm, self._i = model, trace.llm, 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **params: Any) -> LLMResponse:
        if self._i >= len(self._llm):
            raise ReplayDivergence("the agent was asked for more replies than were recorded")
        e = self._llm[self._i]
        self._i += 1
        calls = [ToolCall(c["id"], c["name"], c["arguments"], c.get("raw_arguments", "")) for c in e["tool_calls"]]
        return LLMResponse(e.get("content") or "", calls, e.get("prompt_tokens", 0), e.get("completion_tokens", 0),
                           e.get("latency_s", 0.0), True)  # fmt: skip


class ReplayJudge:
    """Returns the recorded verdicts of the LLM judge (B3), in order."""

    def __init__(self, trace: Trace) -> None:
        self.model = "replayed-judge"
        self._q = [g for g in trace.guard if g.get("extra_llm_calls")]
        self._i = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **params: Any) -> LLMResponse:
        if self._i >= len(self._q):
            raise ReplayDivergence("the judge was asked more often than recorded")
        g = self._q[self._i]
        self._i += 1
        verdict = {"verdict": g["decision"], "reason": (g.get("detail") or {}).get("reason", "")}
        return LLMResponse(json.dumps(verdict), [], g.get("extra_tokens", 0), 0, 0.0, True)


def _norm(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str))


def replay_one(task: Task, recorded: dict[str, Any], trace: Trace | None, physical: Any,
               guard_cfg: GuardConfig | None = None) -> dict[str, Any]:  # fmt: skip
    """Re-run one recorded episode on a sandbox and check it is the same episode."""
    model, config, run = recorded["model"], recorded["config"], recorded.get("run", 0)
    live_results: list[str] = []
    live_guard: list[dict[str, Any]] = []

    def on_event(kind: str, data: dict[str, Any]) -> None:
        if kind == "tool_result":
            live_results.append(data["content"])
        elif kind == "guard":
            live_guard.append(_norm({k: data.get(k) for k in GUARD_KEYS}))

    scripted = model.startswith("scripted:")
    client = None if scripted or trace is None else ReplayClient(trace, model)
    judge = ReplayJudge(trace) if trace is not None and parse_config(config).uses_judge else None
    t0 = time.perf_counter()
    row = run_one(task, model, config, run, client=client, judge=judge, guard_cfg=guard_cfg, on_event=on_event,
                  physical=physical)  # fmt: skip
    mism: list[str] = []
    if trace is not None:
        if live_results != trace.results:
            i = next((i for i, (a, b) in enumerate(zip(live_results, trace.results, strict=False)) if a != b),
                     min(len(live_results), len(trace.results)))  # fmt: skip
            mism.append(f"tool_result[{i}] differs ({len(live_results)} live vs {len(trace.results)} recorded)")
        rec_guard = [_norm({k: g.get(k) for k in GUARD_KEYS}) for g in trace.guard]
        if live_guard != rec_guard:
            mism.append("guard decisions differ")
    mism += [f"{k}: {row.get(k)!r} != {recorded.get(k)!r}" for k in ROW_KEYS if row.get(k) != recorded.get(k)]
    row["replay"] = {"exact": not mism, "mismatches": mism[:5], "seconds": round(time.perf_counter() - t0, 3)}
    return row


# --------------------------------------------------------------------------- the study
@dataclass(frozen=True)
class Job:
    model_dir: str
    config: str
    task_id: str
    run: int
    src: str  # recorded <task>__r<run>.json
    out: str


def plan_jobs(exp: str, models: list[str] | None = None, configs: list[str] | None = None,
              task_ids: set[str] | None = None, out_root: Path | None = None) -> list[Job]:  # fmt: skip
    src_root = results_dir() / "runs" / exp
    out_root = out_root or results_dir() / "physical" / "runs" / exp
    jobs = []
    for p in sorted(src_root.glob("*/*/*.json")):
        model_dir, cfg = p.parts[-3], p.parts[-2]
        task_id, _, r = p.stem.rpartition("__r")
        if models and model_dir not in {m.replace(":", "-").replace("/", "-") for m in models}:
            continue
        if configs and cfg not in configs:
            continue
        if task_ids and task_id not in task_ids:
            continue
        jobs.append(Job(model_dir, cfg, task_id, int(r), str(p), str(out_root / model_dir / cfg / p.name)))
    return sorted(jobs, key=lambda j: (j.task_id, j.model_dir, j.config, j.run))


_W: dict[str, Any] = {}


def _init(base_dir: str | None) -> None:
    _W["tasks"] = {t.id: t for t in load_bench()}
    _W["guard"] = GuardConfig.load()
    _W["opts"] = PhysicalOptions(base_dir=base_dir)


def _work(job: Job) -> dict[str, Any]:
    out = Path(job.out)
    if out.exists():
        return {"job": job.out, "skipped": True}
    recorded = json.loads(Path(job.src).read_text())
    jsonl = Path(job.src).with_suffix(".jsonl")
    trace = load_trace(jsonl) if jsonl.exists() else None  # scripted plans re-run live but are still checked
    if trace is None and not recorded["model"].startswith("scripted:"):
        return {"job": job.out, "error": "no trajectory log"}
    try:
        row = replay_one(_W["tasks"][job.task_id], recorded, trace, _W["opts"], _W["guard"])
    except Exception as exc:  # noqa: BLE001 - a broken replay is reported, never silently dropped
        row = {"error": repr(exc), "task_id": job.task_id, "model": recorded["model"], "config": recorded["config"],
               "run": recorded.get("run", 0)}  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(row, default=str))
    tmp.replace(out)
    return {"job": job.out, "exact": row.get("replay", {}).get("exact"), "error": row.get("error")}


def run_study(jobs: list[Job], workers: int = 8, base_dir: str | None = None,
              progress: Callable[[int, int, dict], None] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    done: list[dict[str, Any]] = []
    if workers <= 1:
        _init(base_dir)
        for j in jobs:
            done.append(_work(j))
            if progress:
                progress(len(done), len(jobs), done[-1])
        return done
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(base_dir,)) as pool:
        for res in pool.map(_work, jobs, chunksize=8):
            done.append(res)
            if progress:
                progress(len(done), len(jobs), res)
    return done


def load_physical_rows(exp: str = "main") -> list[dict[str, Any]]:
    root = results_dir() / "physical" / "runs" / exp
    return [json.loads(p.read_text()) for p in sorted(root.glob("*/*/*.json"))]
