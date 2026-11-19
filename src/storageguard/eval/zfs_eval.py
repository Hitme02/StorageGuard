"""RQ8b: replay the study's executed calls on OpenZFS (in the isolated VM) and compare.

An episode's effect on storage is fixed by its task and the ordered list of calls the
middleware executed (the twin is deterministic), so episodes with the same task and the same
executed calls are one *trajectory*.  The 12,720 replayed episodes reduce to a few thousand
trajectories; each is run once on OpenZFS (``physical.zfs``) and, side by side, on the file
sandbox.  Every episode then inherits its trajectory's result, and the file-sandbox result is
checked against the episode's recorded physical row (so the call-list replay is verified too).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..bench.splits import load_bench, task_twin
from ..paths import results_dir
from ..twin.operations import OPS
from .physical_eval import PhysicalOptions

MEAS_KEYS = ("destroyed", "rolled_back", "cg_split", "outage", "site_loss", "ransomware", "ransomware_admin",
             "phys_data", "phys_outage", "phys_site", "phys_ransom", "phys_protection", "phys_unsafe", "destroyed_gb")  # fmt: skip
FLAGS = ("phys_data", "phys_outage", "phys_site", "phys_ransom", "phys_unsafe")


def zfs_root() -> Path:
    return results_dir() / "zfs"


@dataclass(frozen=True)
class Trajectory:
    key: str  # <task>__<sha8 of the calls>
    task_id: str
    calls: tuple[tuple[str, str], ...]  # (tool, json args), in order

    def call_list(self) -> list[tuple[str, dict]]:
        return [(t, json.loads(a)) for t, a in self.calls]


def _key(task_id: str, calls: list[tuple[str, str]]) -> str:
    return f"{task_id}__{hashlib.sha256(json.dumps(calls).encode()).hexdigest()[:10]}"


def _calls_of(event: dict[str, Any]) -> list[tuple[str, str]]:
    """What reached the twin at one middleware event: the executed steps, or the call the twin refused.

    A call the middleware let through but the twin refused is logged with ``status: error`` and no executed
    steps; it changes nothing in the twin, but it is exactly what ZFS should be asked too.
    """
    steps = event.get("executed") or ([event["action"]] if event.get("status") == "error" else [])
    return [(e["tool"], json.dumps(e["args"], sort_keys=True)) for e in steps]


def _executed_from_trace(path: Path) -> list[tuple[str, str]]:
    out = []
    for line in path.read_text().splitlines():
        r = json.loads(line)
        if r["type"] == "guard":
            out += _calls_of(r)
    return out


def _executed_live(task: Any, model: str, config: str, run: int) -> list[tuple[str, str]]:
    """Scripted plans have no trajectory log: re-run them (deterministic, no LLM) to get the calls."""
    from .runner import run_one

    out: list[tuple[str, str]] = []

    def on_event(kind: str, data: dict[str, Any]) -> None:
        if kind == "guard":
            out.extend(_calls_of(data))

    run_one(task, model, config, run, on_event=on_event)
    return out


def collect(exps: tuple[str, ...] = ("main", "scripted")) -> tuple[dict[str, Trajectory], list[dict[str, Any]]]:
    """Every replayed episode -> its trajectory (only episodes with an exact physical replay)."""
    tasks = {t.id: t for t in load_bench()}
    trajs: dict[str, Trajectory] = {}  # calls the twin refused are included (they change nothing in the twin)
    episodes: list[dict[str, Any]] = []
    for exp in exps:
        for p in sorted((results_dir() / "physical" / "runs" / exp).glob("*/*/*.json")):
            row = json.loads(p.read_text())
            if not row.get("replay", {}).get("exact") or "physical" not in row:
                continue
            src = results_dir() / "runs" / exp / p.parts[-3] / p.parts[-2] / p.name
            trace = src.with_suffix(".jsonl")
            if trace.exists():
                calls = _executed_from_trace(trace)
            elif row["model"].startswith("scripted:"):
                calls = _executed_live(tasks[row["task_id"]], row["model"], row["config"], row.get("run", 0))
            else:
                continue
            key = _key(row["task_id"], calls)
            trajs.setdefault(key, Trajectory(key, row["task_id"], tuple(calls)))
            episodes.append({"exp": exp, "model": row["model"], "config": row["config"], "task_id": row["task_id"],
                             "run": row.get("run", 0), "key": key, "physical_row": str(p.relative_to(results_dir())),
                             "recorded": {k: row["physical"].get(k) for k in MEAS_KEYS}})  # fmt: skip
    return trajs, episodes


# --------------------------------------------------------------------------- running
_W: dict[str, Any] = {}


def _init(base_dir: str | None) -> None:
    from ..physical.zfs import Agent

    _W["tasks"] = {t.id: t for t in load_bench()}
    _W["opts"] = PhysicalOptions(base_dir=base_dir)
    _W["agent"] = Agent()
    _W["base"] = base_dir


def _save(t: Trajectory, res: dict[str, Any]) -> dict[str, Any]:
    res |= {"key": t.key, "task_id": t.task_id, "n_calls": len(t.calls),
            "n_mutating": sum(1 for c, _ in t.calls if OPS.get(c) and OPS[c].mutating)}  # fmt: skip
    for side in ("file", "zfs"):
        if side in res:
            res[side] = {k: res[side].get(k) for k in (*MEAS_KEYS, "drift", "driver_errors", "drills") if k in res[side]}
    out_path = zfs_root() / "runs" / f"{t.key}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, default=str))
    tmp.replace(out_path)
    return {"key": t.key, "error": res.get("error"), "identical": res.get("end", {}).get("identical")}


def _work(group: list[Trajectory]) -> list[dict[str, Any]]:
    """All trajectories of one task: build its ZFS world once, rewind it between trajectories."""
    from ..physical.zfs import ZfsWorld, run_trajectory

    todo = [t for t in group if not (zfs_root() / "runs" / f"{t.key}.json").exists()]
    done = [{"key": t.key, "skipped": True} for t in group if t not in todo]
    if not todo:
        return done
    task = _W["tasks"][todo[0].task_id]
    try:
        world = ZfsWorld(_W["agent"], task_twin(task), _W["base"])
    except Exception as exc:  # noqa: BLE001 - reported per trajectory
        return done + [_save(t, {"error": f"world: {exc!r}"}) for t in todo]
    try:
        for t in todo:
            calls = t.call_list()
            t0 = time.perf_counter()
            try:
                res = run_trajectory(world, lambda: task_twin(task), calls, task.allowed_changes,
                                     _W["opts"].reference_for(task, calls))  # fmt: skip
                res["world"] = {"build_s": round(world.t_build, 2), "start_drift": world.start_drift[:3]}
            except Exception as exc:  # noqa: BLE001 - a failed trajectory is reported, never silently dropped
                res = {"error": repr(exc)}
            res["wall_s"] = round(time.perf_counter() - t0, 2)
            done.append(_save(t, res))
    finally:
        world.close()
    return done


def run_zfs_study(trajs: list[Trajectory], workers: int = 4, base_dir: str | None = None,
                  progress: Callable[[int, int, dict], None] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    from ..physical.zfs import reap

    reap()  # pools left by an interrupted earlier run (only ones this tool created)
    record_environment()
    groups: dict[str, list[Trajectory]] = {}
    for t in sorted(trajs, key=lambda t: (t.task_id, t.key)):
        groups.setdefault(t.task_id, []).append(t)
    jobs = sorted(groups.values(), key=len, reverse=True)  # big tasks first: better load balance
    done: list[dict[str, Any]] = []

    def note(results: list[dict[str, Any]]) -> None:
        for r in results:
            done.append(r)
            if progress:
                progress(len(done), len(trajs), r)

    if workers <= 1:
        _init(base_dir)
        try:
            for g in jobs:
                note(_work(g))
        finally:
            _W["agent"].close()
        return done
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(base_dir,)) as pool:
        for results in pool.map(_work, jobs):
            note(results)
    reap()  # the worker processes' agents end with the pool; make sure nothing is left in the VM
    return done


def record_environment() -> dict[str, Any]:
    """Lima VM facts and the agent's view of the guest (versions, mount types), for the report."""
    import subprocess

    from ..physical.zfs import Agent, check_vm

    vm = check_vm()
    a = Agent()
    try:
        env = a("env")
    finally:
        a.close()
    lima = subprocess.run(["limactl", "--version"], capture_output=True, text=True, check=False).stdout.strip()
    out = {"vm": vm, "lima": lima, "plain": True, "host_mounts": 0, **{k: v for k, v in env.items() if k != "ok"}}
    zfs_root().mkdir(parents=True, exist_ok=True)
    (zfs_root() / "environment.json").write_text(json.dumps(out, indent=1))
    return out


def save_index(trajs: dict[str, Trajectory], episodes: list[dict[str, Any]]) -> None:
    root = zfs_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "trajectories.json").write_text(json.dumps({k: {"task_id": t.task_id, "calls": t.calls} for k, t in trajs.items()}))
    (root / "episodes.json").write_text(json.dumps(episodes))


def load_index() -> tuple[dict[str, Trajectory], list[dict[str, Any]]]:
    root = zfs_root()
    raw = json.loads((root / "trajectories.json").read_text())
    trajs = {k: Trajectory(k, v["task_id"], tuple(tuple(c) for c in v["calls"])) for k, v in raw.items()}  # type: ignore[misc]
    return trajs, json.loads((root / "episodes.json").read_text())


def load_results() -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text()) for p in sorted((zfs_root() / "runs").glob("*.json"))}
