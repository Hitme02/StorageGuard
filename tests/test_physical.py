"""Physical execution: sandbox safety, the storage driver's fidelity, file-level loss measurement, replay."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from storageguard.bench.schema import AllowedChanges
from storageguard.bench.splits import load_bench
from storageguard.eval.harm_oracle import evaluate_harms
from storageguard.eval.physical_eval import PhysicalOptions, load_trace, replay_one
from storageguard.eval.runner import run_one
from storageguard.paths import results_dir
from storageguard.physical import image as img
from storageguard.physical.measure import read_back
from storageguard.physical.sandbox import MARKER, Sandbox, SandboxError
from storageguard.physical.session import PhysicalSession, reference

PHYS = Path(__file__).parents[1] / "src" / "storageguard" / "physical"


# --------------------------------------------------------------------------- sandbox safety
def test_sandbox_refuses_paths_outside_its_root(tmp_path):
    sb = Sandbox.create(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious")
    for bad in ("../outside/keep.txt", "/etc/hosts", "a/../../outside/keep.txt", ""):
        with pytest.raises(SandboxError):
            sb.write(bad, "x")
        with pytest.raises(SandboxError):
            sb.unlink(bad)
    os.symlink(outside, sb.root / "link")
    with pytest.raises(SandboxError):
        sb.write("link/keep.txt", "overwritten")
    with pytest.raises(SandboxError):
        sb.rmtree("link")
    with pytest.raises(SandboxError):
        sb.copy(tmp_path)  # a sandbox holding a symlink is never copied
    assert (outside / "keep.txt").read_text() == "precious"


def test_destructive_calls_need_the_marker(tmp_path):
    sb = Sandbox.create(tmp_path)
    sb.write("a/b.img", "x")
    (sb.root / MARKER).unlink()
    with pytest.raises(SandboxError):
        sb.unlink("a/b.img")
    with pytest.raises(SandboxError):
        sb.rmtree("a")
    with pytest.raises(SandboxError):
        Sandbox(tmp_path, "emulated")  # not a sandbox directory at all


def test_a_locked_file_really_cannot_be_deleted(tmp_path):
    sb = Sandbox.create(tmp_path)
    sb.write("v/snapshots/s.img", "data")
    sb.lock("v/snapshots/s.img")
    with pytest.raises(PermissionError):
        sb.unlink("v/snapshots/s.img")
    with pytest.raises(PermissionError):
        sb.rmtree("v")
    with pytest.raises(PermissionError):
        sb.write("v/snapshots/s.img", "ransom")
    if sb.lock_mode == "os":  # the kernel refuses, not just the sandbox
        with pytest.raises(PermissionError):
            os.unlink(sb.root / "v" / "snapshots" / "s.img")
    copy = sb.copy(tmp_path)
    with pytest.raises(PermissionError):
        copy.unlink("v/snapshots/s.img")  # locks survive a copy
    assert copy.rmtree("v", destroy_locked=True)  # a fire does not respect locks
    copy.destroy()
    sb.unlock("v/snapshots/s.img")
    assert sb.unlink("v/snapshots/s.img") == 4
    sb.destroy()
    assert not sb.root.exists()


# --------------------------------------------------------------------------- image format
def test_image_commits_are_verified_on_read():
    lines = img.segment("data-1", 1000, 2000)
    good = img.parse(img.render(lines))
    assert good.newest == {"data-1": 2000} and good.bad_commits == 0
    tampered = img.render([lines[0].replace(lines[0][-1], "0" if lines[0][-1] != "0" else "1"), *lines[1:]])
    assert img.parse(tampered).bad_commits == 1
    enc = img.parse(img.render([img.encrypt_lines(lines, "k")]))
    assert enc.newest == {} and enc.encrypted_segments == 1
    assert not img.parse("not an image").valid


# --------------------------------------------------------------------------- world + driver
def _clean_points(state):
    """Newest clean point per lineage, computed from the twin's ground truth (what the files must say)."""
    out: dict[str, int] = {}

    def add(lin, t, clean):
        if clean:
            out[lin] = max(t, out.get(lin, -1))

    for v in state.volumes.values():
        add(v.data_id, v.content_time, v.true_clean)
        if v.origin_data_id:
            add(v.origin_data_id, v.origin_time or 0, bool(v.origin_clean))
    for s in state.snapshots.values():
        add(s.data_id, s.point_in_time, s.true_clean)
    for b in state.backups.values():
        add(b.data_id, b.point_in_time, b.true_clean)
    return out


def test_initial_files_hold_exactly_the_scenario_data(twin, tmp_path):
    s = PhysicalSession(twin.clone(), str(tmp_path))
    assert s.inventory_ok_at_start
    assert read_back(s.sb).newest == _clean_points(twin.state)
    assert s.sb.locks == set()
    s.close()


def test_driver_really_deletes_and_measures_like_the_oracle(twin, ids, tmp_path):
    initial = twin.state.fast_copy()
    s = PhysicalSession(twin.clone(), str(tmp_path))
    t, ex = s.twin, s.twin.executor
    repo_dir = ex.loc[ids.repo]
    assert t.execute("delete_volume", {"volume_id": ids.repo, "force": True})["ok"]
    assert not s.sb.exists(repo_dir)  # the repository and the CRM backups stored in it are gone from disk
    assert t.execute("restore_snapshot", {"volume_id": ids.crm, "snapshot_id": ids.crm_snap})["ok"]
    assert t.execute("delete_volume", {"volume_id": ids.dev, "force": True})["ok"]
    assert ex.drift == [] and ex.errors == []
    phys = s.finish(AllowedChanges(), reference(twin.clone(), AllowedChanges(), [], str(tmp_path)))
    s.close()
    harms = {(h.type, h.code, h.entity) for h in evaluate_harms(initial, t.state)}
    destroyed = {d["owner"] for d in phys["destroyed"]}
    assert destroyed == {e for k, c, e in harms if (k, c) == ("H1", "data_destroyed")} == {ids.repo, ids.dev}
    assert {d["owner"] for d in phys["rolled_back"]} == {ids.crm}
    assert {o["volume"] for o in phys["outage"]} == {ids.dev}
    assert phys["files_deleted"] == 5 and phys["files_overwritten"] == 1


def test_drills_turn_lost_protection_into_lost_data(twin, ids, tmp_path):
    twin.state.snapshots[ids.cg_sets[2][0]].immutable = True  # the newest ERP data snapshot is locked
    allowed = AllowedChanges()
    ref = reference(twin.clone(), allowed, [], str(tmp_path))

    def episode(tool, args):
        s = PhysicalSession(twin.clone(), str(tmp_path))
        assert s.twin.execute(tool, args)["ok"]
        phys = s.finish(allowed, ref)
        s.close()
        assert phys["destroyed"] == [] and phys["rolled_back"] == []  # nothing is lost yet...
        assert phys["phys_protection"] == 1
        return phys

    paused = episode("pause_replication", {"link_id": ids.link})
    assert {x["owner"] for x in paused["site_loss"]} == {ids.data}  # ...until the primary site fails
    assert paused["ransomware"] == []  # (a paused replica also escapes the ransomware: pausing is not all bad)
    unlocked = episode("set_snapshot_immutable", {"snapshot_id": ids.cg_sets[2][0], "immutable": False})
    assert ids.data in {x["owner"] for x in unlocked["ransomware_admin"]}  # ...or an attacker deletes the snapshot
    assert unlocked["site_loss"] == []


def test_authorised_deletions_are_not_counted(twin, ids, tmp_path):
    allowed = AllowedChanges(deletable_volumes=[ids.scratch], expendable_data=[ids.scratch])
    s = PhysicalSession(twin.clone(), str(tmp_path))
    assert s.twin.execute("delete_volume", {"volume_id": ids.scratch})["ok"]
    calls = [("delete_volume", {"volume_id": ids.scratch})]
    phys = s.finish(allowed, reference(twin.clone(), allowed, calls, str(tmp_path)))
    s.close()
    assert phys["files_deleted"] == 1 and phys["phys_unsafe"] == 0


def test_scripted_plans_run_on_files_without_drift(tmp_path):
    tasks = load_bench(split="dev")
    pick = {}
    for t in tasks:  # one task per trap type plus a routine one
        pick.setdefault(t.trap_type or t.category, t)
    opts = PhysicalOptions(base_dir=str(tmp_path))
    agree = 0
    for t in list(pick.values())[:10]:
        for model in ("scripted:naive", "scripted:reference"):
            row = run_one(t, model, "noguard", physical=opts)
            p = row["physical"]
            assert p["drift"] == 0 and p["driver_errors"] == [], (t.id, model, p["drift_detail"], p["driver_errors"])
            agree += ("H1:data_destroyed" in row["harm_codes"]) == bool(p["destroyed"])
    assert agree == 2 * min(10, len(pick))
    assert not any(p.name.startswith("sg-sandbox-") for p in tmp_path.iterdir())  # every sandbox was cleaned up


# --------------------------------------------------------------------------- isolation + replay
def test_physical_layer_is_independent_of_guard_graph_and_oracle():
    for path in PHYS.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = ("." * node.level) + (node.module or "")
                assert not any(x in mod.split(".") for x in ("guard", "graph", "eval", "agent")), (path.name, mod)
                assert mod not in ("..bench.splits", "..bench.validate"), (path.name, mod)
            if path.name != "world.py" and isinstance(node, ast.Attribute):
                assert node.attr not in {"data_id", "content_time", "point_in_time", "true_clean", "origin_data_id",
                                         "origin_time", "origin_clean"}, (path.name, node.attr)  # fmt: skip


def _recorded(config: str) -> Path | None:
    root = results_dir() / "runs" / "main" / "llama3.1-8b" / config
    return next(iter(sorted(root.glob("*.jsonl"))), None) if root.exists() else None


@pytest.mark.parametrize("config", ["storageguard", "llm_judge", "noguard"])
def test_recorded_episode_replays_exactly_on_files(config, tmp_path):
    jsonl = _recorded(config)
    if jsonl is None:
        pytest.skip("no recorded main-study episodes in results/")
    recorded = json.loads(jsonl.with_suffix(".json").read_text())
    task = next(t for t in load_bench() if t.id == recorded["task_id"])
    row = replay_one(task, recorded, load_trace(jsonl), PhysicalOptions(base_dir=str(tmp_path)))
    assert row["replay"]["exact"], row["replay"]
    assert row["physical"]["drift"] == 0
