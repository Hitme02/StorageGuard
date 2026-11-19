"""Experiment runner: models x configs x tasks x runs, resumable, concurrent, cached."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..agent.cache import ResponseCache
from ..agent.llm_client import LLMClient, ModelSpec, ScriptedClient, load_models, make_client
from ..agent.loop import MAX_STEPS, run_episode
from ..bench.schema import Task
from ..bench.splits import load_bench, task_twin
from ..guard.config import GuardConfig
from ..paths import config_dir, results_dir
from .baselines import make_middleware, parse_config
from .harm_oracle import dry_run_harms
from .human_sim import SimulatedHuman
from .metrics import NOT_EXECUTED, score_episode

AVG_STEPS_ESTIMATE = 9  # used only for the pre-run LLM-call estimate
AVG_MUTATIONS_ESTIMATE = 2.5


@dataclass(frozen=True)
class EpisodeKey:
    model: str
    config: str
    task_id: str
    run: int

    def path(self, root: Path) -> Path:
        safe = self.config.replace("@", "__").replace("=", "-").replace(",", "_")
        return root / self.model.replace(":", "-").replace("/", "-") / safe / f"{self.task_id}__r{self.run}.json"


def scripted_client(model: str, task: Task) -> LLMClient | None:
    if model == "scripted:reference":
        return ScriptedClient(task.reference_safe_plan, model)
    if model == "scripted:naive":
        return ScriptedClient(task.naive_plan, model)
    return None


def run_one(task: Task, model: str, config: str, run: int = 0, client: LLMClient | None = None,
            judge: LLMClient | None = None, guard_cfg: GuardConfig | None = None, out: Path | None = None,
            max_steps: int = MAX_STEPS, context_chars: int = 36000, on_event: Callable | None = None,
            physical: Any = None) -> dict[str, Any]:  # fmt: skip
    """Run and score a single episode; optionally write ``<out>.json`` + ``<out>.jsonl``.

    ``physical`` (a :class:`storageguard.eval.physical_eval.PhysicalOptions`) also executes every
    call the middleware lets through on a file-backed sandbox and adds ``row["physical"]``.
    """
    cs = parse_config(config)
    twin = task_twin(task)
    session = None
    if physical is not None:
        from ..physical.session import PhysicalSession

        session = PhysicalSession(twin, physical.base_dir, physical.keep)
        twin = session.twin
    initial = twin.state.fast_copy()
    human = SimulatedHuman(task, initial, cs.human)
    client = client or scripted_client(model, task)
    if client is None:
        raise ValueError(f"no client for model '{model}'")
    params = {} if model.startswith("scripted:") else {"seed": run, "sg_run": run}
    mw = make_middleware(cs, human, judge or client, guard_cfg, params)
    stops: list[dict[str, Any]] = []

    def label_stops(kind: str, data: dict[str, Any]) -> None:
        """Evaluation-side: ask the oracle whether each stopped action would actually have caused harm.

        The twin is unchanged when a call is stopped, so a dry run from the current state is exact.
        """
        if kind == "guard" and data.get("status") in NOT_EXECUTED:
            act = data["action"]
            harms = dry_run_harms(twin, initial, [(act["tool"], act["args"])], task.allowed_changes)
            stops.append({"tool": act["tool"], "args": act["args"], "decision": data["decision"], "status": data["status"],
                          "harmful": bool(harms), "harm_codes": sorted({f"{h.type}:{h.code}" for h in harms})})  # fmt: skip
        if on_event:
            on_event(kind, data)

    try:
        traj = run_episode(task.instruction, twin, client, mw, human, max_steps=max_steps, context_chars=context_chars,
                           log_path=out.with_suffix(".jsonl") if out else None, llm_params=params, on_event=label_stops)  # fmt: skip
        row = score_episode(task, initial, twin, traj, stops=stops, model=model, config=config, run=run)
        row["reviews"], row["questions"], row["summary"] = human.reviews, human.questions, traj.summary
        if session is not None:
            executed = [(s["tool"], s["args"]) for e in traj.guard_events for s in e.executed]
            row["physical"] = session.finish(task.allowed_changes, physical.reference_for(task, executed))
            row["physical"]["sandbox"] = str(session.kept.root) if session.kept else None
    finally:
        if session is not None:
            session.close()
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(row, default=str))
        tmp.replace(out)  # the .json is the completion marker used for resuming
    return row


def load_experiment(name_or_path: str) -> dict[str, Any]:
    p = Path(name_or_path)
    if not p.exists():
        p = config_dir() / "experiments" / f"{name_or_path}.yaml"
    exp = yaml.safe_load(p.read_text())
    exp.setdefault("name", p.stem)
    return exp


def plan_episodes(exp: dict[str, Any], split: str | None = None, limit: int | None = None,
                  categories: list[str] | None = None, models: list[str] | None = None,
                  configs: list[str] | None = None) -> tuple[list[tuple[Task, EpisodeKey]], Path]:  # fmt: skip
    tasks = load_bench(split=split or exp.get("split", "test"), categories=categories or exp.get("categories"),
                       limit=limit or exp.get("limit"))  # fmt: skip
    root = results_dir() / "runs" / exp["name"]
    todo = []
    for model in models or exp["models"]:
        for cfg in configs or exp["configs"]:
            runs = exp.get("runs_per_config", {}).get(cfg, exp.get("runs", 1))
            if model.startswith("scripted:"):
                if parse_config(cfg).uses_judge:
                    continue
                runs = 1
            for r in range(runs):
                todo += [(t, EpisodeKey(model, cfg, t.id, r)) for t in tasks]
    return todo, root


def estimate_calls(todo: list[tuple[Task, EpisodeKey]]) -> int:
    n = 0
    for _, k in todo:
        if k.model.startswith("scripted:"):
            continue
        n += AVG_STEPS_ESTIMATE + (AVG_MUTATIONS_ESTIMATE if parse_config(k.config).uses_judge else 0)
    return int(n)


async def run_experiment(exp: dict[str, Any], split: str | None = None, limit: int | None = None,
                         categories: list[str] | None = None, models: list[str] | None = None,
                         configs: list[str] | None = None, progress: Callable[[int, int, dict], None] | None = None,
                         quiet: bool = False) -> list[dict[str, Any]]:  # fmt: skip
    """Run every missing episode of an experiment matrix (resumable)."""
    todo, root = plan_episodes(exp, split, limit, categories, models, configs)
    pending = [(t, k) for t, k in todo if not k.path(root).exists()]
    if not quiet:
        print(f"[{exp['name']}] {len(todo)} episodes, {len(todo) - len(pending)} already complete, "
              f"~{estimate_calls(pending)} LLM calls to make (before cache hits)")  # fmt: skip
    needs_llm = any(not k.model.startswith("scripted:") for _, k in pending)
    specs = load_models() if needs_llm else {}  # scripted runs need no model registry
    cache = ResponseCache()
    guard_cfg = GuardConfig.load()
    clients: dict[str, LLMClient] = {}
    sems: dict[str, asyncio.Semaphore] = {}

    def spec_of(model: str) -> ModelSpec | None:
        return None if model.startswith("scripted:") else specs[model]

    for model in {k.model for _, k in pending}:
        sp = spec_of(model)
        if sp is not None:
            clients[model] = make_client(sp, cache)
            sems.setdefault(sp.base_url, asyncio.Semaphore(sp.concurrency))
    sems["scripted"] = asyncio.Semaphore(8)
    workers = max([8, *(specs[m].concurrency for m in clients)]) + 4
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=workers))
    done = 0
    t0 = time.time()

    async def worker(task: Task, key: EpisodeKey) -> None:
        nonlocal done
        sp = spec_of(key.model)
        sem = sems[sp.base_url if sp else "scripted"]
        async with sem:
            try:
                row = await asyncio.to_thread(
                    run_one, task, key.model, key.config, key.run, clients.get(key.model), None, guard_cfg,
                    key.path(root), exp.get("max_steps", MAX_STEPS), sp.context_chars if sp else 36000)  # fmt: skip
            except Exception as exc:  # noqa: BLE001 - one broken episode must not kill a long run
                row = {"error": repr(exc), "task_id": task.id, "model": key.model, "config": key.config}
                print(f"  ! {key.model} {key.config} {task.id}: {exc!r}")
        done += 1
        if progress:
            progress(done, len(pending), row)
        elif not quiet and (done % 10 == 0 or done == len(pending)):
            rate = (time.time() - t0) / done
            print(f"  {done}/{len(pending)}  {rate:.1f}s/episode  eta {rate * (len(pending) - done) / 60:.0f} min", flush=True)

    await asyncio.gather(*(worker(t, k) for t, k in pending))
    cache.close()
    return [json.loads(k.path(root).read_text()) for _, k in todo if k.path(root).exists()]


def load_rows(exp_name: str) -> list[dict[str, Any]]:
    root = results_dir() / "runs" / exp_name
    return [json.loads(p.read_text()) for p in sorted(root.rglob("*.json"))]
