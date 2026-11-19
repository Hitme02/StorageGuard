"""Benchmark generation, persistence and the dev/test split.

Tasks are stored as small JSON records; the initial twin of a task is rebuilt
deterministically from ``(task_type, scenario_seed)`` and checked against the
stored state hash, so the benchmark file stays tiny and tamper-evident.
"""

from __future__ import annotations

import hashlib
import json
import random
import zlib
from collections import Counter, defaultdict
from pathlib import Path

from ..paths import bench_dir
from ..twin.state import StorageTwin
from .scenario import generate_scenario
from .schema import Task
from .traps import RECIPES
from .validate import validate_task

HOLDOUT_TRAPS = ("T11", "T12")  # never in dev: measures generalisation of dev-tuned thresholds
DEV_FRACTION = 0.3


def _task_id(recipe: str, seed: int) -> str:
    return "task-" + hashlib.sha1(f"{recipe}|{seed}".encode()).hexdigest()[:8]


def build(recipe_name: str, seed: int) -> tuple[Task, StorageTwin] | None:
    """Instantiate one recipe on the scenario generated from ``seed``."""
    rec = RECIPES[recipe_name]
    sc = generate_scenario(seed)
    spec = rec.fn(sc, random.Random(f"{recipe_name}|{seed}"))
    if spec is None:
        return None
    task = Task(
        id=_task_id(recipe_name, seed), category=rec.category, trap_type=rec.trap_type, task_type=recipe_name,  # type: ignore[arg-type]
        scenario_seed=seed, instruction=spec.instruction, clarifications=spec.clarifications, goal=spec.goal,
        allowed_changes=spec.allowed, expected_outcome=spec.expected, reference_safe_plan=spec.reference,  # type: ignore[arg-type]
        naive_plan=spec.naive, difficulty=spec.difficulty, notes=spec.notes, state_hash=sc.twin.state.content_hash(),  # type: ignore[arg-type]
    )  # fmt: skip
    return task, sc.twin


def task_twin(task: Task) -> StorageTwin:
    """Rebuild the initial twin of a stored task (verifies the state hash)."""
    built = build(task.task_type, task.scenario_seed)
    if built is None:
        raise ValueError(f"cannot rebuild scenario for {task.id}")
    twin = built[1]
    if task.state_hash and twin.state.content_hash() != task.state_hash:
        raise ValueError(f"{task.id}: regenerated scenario does not match the stored state hash")
    return twin


def generate_bench(scale: float = 1.0, base_seed: int = 0, max_tries: int = 400) -> tuple[list[Task], dict[str, int]]:
    """Generate valid tasks: ``round(count * scale)`` per recipe (150 tasks at scale 1)."""
    tasks: list[Task] = []
    rejected: Counter[str] = Counter()
    for name, rec in RECIPES.items():
        want = max(1, round(rec.count * scale))
        got = i = 0
        while got < want and i < max_tries * want:
            seed = zlib.crc32(f"{name}|{base_seed}|{i}".encode()) % 1_000_000
            i += 1
            built = build(name, seed)
            if built is None:
                rejected[f"{name}:unsuitable"] += 1
                continue
            task, twin = built
            if not validate_task(task, twin).ok:
                rejected[f"{name}:invalid"] += 1
                continue
            tasks.append(task)
            got += 1
        if got < want:
            raise RuntimeError(f"recipe {name}: only {got}/{want} valid tasks after {i} seeds")
    return assign_splits(tasks, seed=base_seed), dict(rejected)


def assign_splits(tasks: list[Task], seed: int = 0, dev_fraction: float = DEV_FRACTION) -> list[Task]:
    """30/70 dev/test, stratified by category, spread over task families; T11/T12 held out of dev."""
    rng = random.Random(seed)
    by_cat: dict[str, list[Task]] = defaultdict(list)
    for t in tasks:
        by_cat[t.category].append(t)
    dev: set[str] = set()
    for cat in sorted(by_cat):
        group = by_cat[cat]
        n_dev = round(dev_fraction * len(group))
        families: dict[str, list[Task]] = defaultdict(list)
        for t in group:
            if t.trap_type in HOLDOUT_TRAPS:
                continue
            families[t.trap_type or t.task_type].append(t)
        for fam in families.values():
            rng.shuffle(fam)
        order = sorted(families)
        while n_dev > 0 and any(families.values()):
            for key in order:  # round-robin keeps every family represented in both splits
                if n_dev > 0 and len(families[key]) > 1:
                    dev.add(families[key].pop().id)
                    n_dev -= 1
            if all(len(f) <= 1 for f in families.values()):
                break
    return [t.model_copy(update={"split": "dev" if t.id in dev else "test"}) for t in tasks]


def count_table(tasks: list[Task]) -> list[tuple[str, str, int, int, int]]:
    """Rows of (category, trap type or '-', total, dev, test)."""
    rows: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for t in tasks:
        r = rows[(t.category, t.trap_type or "-")]
        r[0] += 1
        r[1 if t.split == "dev" else 2] += 1
    order = {"routine": 0, "ambiguous": 1, "trap": 2, "injection": 3}
    keys = sorted(rows, key=lambda k: (order[k[0]], k[1][:1], int(k[1][1:]) if k[1] != "-" else 0))
    return [(c, t, *rows[(c, t)]) for c, t in keys]


def save_bench(tasks: list[Task], path: Path | None = None) -> Path:
    path = path or bench_dir() / "tasks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([t.model_dump(mode="json") for t in tasks], indent=1))
    return path


def load_bench(path: Path | None = None, split: str | None = None, categories: list[str] | None = None,
               limit: int | None = None) -> list[Task]:  # fmt: skip
    path = path or bench_dir() / "tasks.json"
    tasks = [Task.model_validate(x) for x in json.loads(path.read_text())]
    if split and split != "all":
        tasks = [t for t in tasks if t.split == split]
    if categories:
        tasks = [t for t in tasks if t.category in categories]
    return tasks[:limit] if limit else tasks
