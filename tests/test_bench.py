"""Phase 4: benchmark generation, validation, goals, splits."""

import json
from collections import Counter

import pytest

from storageguard.bench.goals import ALL, ANY, P, evaluate
from storageguard.bench.schema import Task, ToolPattern
from storageguard.bench.splits import HOLDOUT_TRAPS, assign_splits, build, generate_bench, task_twin
from storageguard.bench.traps import RECIPES, trap_types
from storageguard.bench.validate import validate_task
from storageguard.twin import capture_public
from storageguard.twin.models import INTERNAL_FIELDS


@pytest.fixture(scope="module")
def bench():
    return generate_bench()[0]


def test_benchmark_size_and_composition(bench):
    cats = Counter(t.category for t in bench)
    assert len(bench) >= 150
    assert cats["routine"] >= 45 and cats["ambiguous"] >= 25 and cats["trap"] >= 65 and cats["injection"] >= 15
    per_trap = Counter(t.trap_type for t in bench if t.category == "trap")
    assert set(per_trap) == {f"T{i}" for i in range(1, 13)} and min(per_trap.values()) >= 5
    assert {t.trap_type for t in bench if t.category == "injection"} == {"P1", "P2", "P3"}
    assert trap_types() == [f"P{i}" for i in (1, 2, 3)] + [f"T{i}" for i in range(1, 13)]


def test_every_task_is_valid(bench):
    for t in bench:
        res = validate_task(t, task_twin(t))
        assert res.ok, (t.id, t.task_type, res.errors)
        assert res.reference_harms == []
        if t.category in ("trap", "injection"):
            assert res.naive_harms
        if t.category == "routine":
            assert t.naive_plan == t.reference_safe_plan


def test_generation_is_deterministic(bench):
    again = generate_bench()[0]
    assert [t.model_dump() for t in again] == [t.model_dump() for t in bench]


def test_splits(bench):
    n_dev = sum(t.split == "dev" for t in bench)
    assert abs(n_dev / len(bench) - 0.3) < 0.02
    assert not [t for t in bench if t.split == "dev" and t.trap_type in HOLDOUT_TRAPS]
    for cat in ("routine", "ambiguous", "trap", "injection"):
        group = [t for t in bench if t.category == cat]
        assert abs(sum(t.split == "dev" for t in group) / len(group) - 0.3) < 0.06
    dev_traps = {t.trap_type for t in bench if t.split == "dev" and t.category == "trap"}
    assert dev_traps == {f"T{i}" for i in range(1, 11)}
    assert [t.split for t in assign_splits(bench)] == [t.split for t in bench]


def test_ids_and_instructions_do_not_leak(bench):
    assert len({t.id for t in bench}) == len(bench)
    for t in bench:
        assert t.id.startswith("task-") and len(t.id) == 13
        low = t.instruction.lower()
        for word in ("trap", "naive", "benchmark", *([t.task_type] if "_" in t.task_type else [])):
            assert word not in low
        assert (t.trap_type or "zz").lower() not in t.id


def test_public_state_of_tasks_has_no_internal_or_label_fields(bench):
    for t in bench[::9]:
        ps = capture_public(task_twin(t))
        blob = json.dumps({k: getattr(ps, k) for k in ps.ENTITY_KINDS})
        assert not any(f'"{f}"' in blob for fs in INTERNAL_FIELDS.values() for f in fs)
        assert "trap" not in blob.lower() and "naive" not in blob.lower()
        assert "_" not in t.task_type or t.task_type not in blob


def test_task_json_roundtrip_and_hash_check(bench):
    t = bench[0]
    again = Task.model_validate(json.loads(json.dumps(t.model_dump(mode="json"))))
    assert again == t
    with pytest.raises(ValueError):
        task_twin(t.model_copy(update={"state_hash": "0" * 64}))


def test_scales_to_larger_benchmarks():
    tasks, _ = generate_bench(scale=1.6, base_seed=3)
    assert len(tasks) >= 230 and len({t.id for t in tasks}) == len(tasks)


def test_each_recipe_builds():
    for name in RECIPES:
        assert any(build(name, s) is not None for s in range(5)), name


def test_goal_predicates(twin, ids):
    i = twin.state.fast_copy()
    twin.execute("create_volume", {"name": "nv", "pool_id": ids.pool1, "size_gb": 50})
    twin.execute("map_volume", {"volume_id": "nv" if False else twin.resolve_args("x", {"volume_id": "nv"})["volume_id"], "host_id": ids.h1})
    twin.execute("delete_volume", {"volume_id": ids.scratch, "to_recycle_bin": True})
    twin.execute("pause_replication", {"link_id": ids.link})
    f = twin.state
    assert evaluate(P("volume_exists", name="nv", min_size_gb=50, pool_id=ids.pool1), i, f)
    assert not evaluate(P("volume_exists", name="nv", min_size_gb=51), i, f)
    assert evaluate(P("mapped", host_id=ids.h1, volume_name="nv"), i, f)
    assert evaluate(P("not_exists", kind="volumes", id=ids.scratch), i, f) and not evaluate(P("purged", volume_id=ids.scratch), i, f)
    assert evaluate(P("replication_state", link_id=ids.link, state="paused"), i, f)
    assert not evaluate(P("capacity_freed", pool_id=ids.pool2, gb=1), i, f)
    assert evaluate(ALL(P("exists", kind="volumes", id=ids.dev), ANY(P("immutable", snapshot_id=ids.dev_snap), P("hot_spares", pool_id=ids.pool1, min=2))), i, f)
    assert evaluate({"not": P("restored", volume_id=ids.dev, snapshot_id=ids.dev_snap)}, i, f)
    assert evaluate(P("snapshot_exists", volume_id=ids.dev), i, f) and not evaluate(P("snapshot_exists", volume_id=ids.dev, after=f.now), i, f)
    assert evaluate(P("zone_contains", wwpn=f.hosts[ids.h1].initiators[0], array_id=ids.arr_p, fabric="A"), i, f)
    assert evaluate(P("backups_intact", repository=ids.repo), i, f)


def test_tool_pattern_matching():
    p = ToolPattern(tool="delete_volume", args={"volume_id": "vol-1"})
    assert p.matches("delete_volume", {"volume_id": "vol-1", "force": True})
    assert not p.matches("delete_volume", {"volume_id": "vol-2"}) and not p.matches("purge_recycle_bin", {"volume_id": "vol-1"})
