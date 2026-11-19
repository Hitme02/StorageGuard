"""Restore-semantics sensitivity: the variant twin follows what OpenZFS 2.2.2 did in the lab VM."""

from __future__ import annotations

from collections import Counter

import pytest

from storageguard.bench.splits import load_bench, task_twin
from storageguard.eval.restore_sensitivity import _variant, excess_loss
from storageguard.physical.executor import PhysicalTwin
from storageguard.physical.sandbox import Sandbox
from storageguard.physical.world import materialise
from storageguard.twin.public import to_public


def _setup(tmp_path):
    """A dev-split volume with at least two snapshots, none locked and none cloned."""
    for task in load_bench(split="dev"):
        twin = task_twin(task)
        st = twin.state
        cloned = {v.parent_snapshot_id for v in st.volumes.values()}
        by_vol: dict[str, list[dict]] = {}
        for s in st.snapshots.values():
            p = to_public("snapshots", s)
            by_vol.setdefault(p["volume_id"], []).append(p)
        for vid, snaps in sorted(by_vol.items()):
            vol = to_public("volumes", st.volumes[vid])
            if (len(snaps) >= 2 and vol["role"] == "primary" and not any(x["immutable"] for x in snaps)
                    and not cloned & {x["id"] for x in snaps}):  # fmt: skip
                snaps.sort(key=lambda x: x["created_at"])
                sb = Sandbox.create(str(tmp_path))
                materialise(st, sb)
                return PhysicalTwin.attach(twin, sb), sb, vid, [x["id"] for x in snaps]
    raise AssertionError("no suitable volume in the dev split")


@pytest.fixture
def world(tmp_path):
    pt, sb, vid, snaps = _setup(tmp_path)
    yield pt, vid, snaps
    sb.destroy()


def _restore(vt, vid, sid):
    return vt.execute("restore_snapshot", {"volume_id": vid, "snapshot_id": sid})


def _content(vt, vid):
    return vt.state.volumes[vid].content_time


def test_keep_restores_and_keeps_newer_snapshots(world):
    pt, vid, snaps = world
    vt = _variant(pt, "keep", Counter())
    assert _restore(vt, vid, snaps[0])["ok"]
    assert set(snaps) <= set(vt.state.snapshots)


def test_refuse_changes_nothing_while_newer_snapshots_exist(world):
    pt, vid, snaps = world
    stats: Counter = Counter()
    vt = _variant(pt, "refuse", stats)
    before = _content(vt, vid)
    out = _restore(vt, vid, snaps[0])
    assert not out["ok"] and "more recent snapshots" in out["error"]
    assert _content(vt, vid) == before and set(snaps) <= set(vt.state.snapshots) and stats["refused"] == 1
    assert _restore(vt, vid, snaps[-1])["ok"]  # the newest one has nothing newer


def test_destroy_newer_destroys_then_restores(world):
    pt, vid, snaps = world
    stats: Counter = Counter()
    vt = _variant(pt, "destroy_newer", stats)
    now = vt.state.now
    assert _restore(vt, vid, snaps[0])["ok"]
    assert snaps[0] in vt.state.snapshots and not set(snaps[1:]) & set(vt.state.snapshots)
    assert stats["snapshots_destroyed"] == len(snaps) - 1 and stats["rolled_back"] == 1
    assert vt.state.now >= now  # the destroys take no extra simulated time beyond the restore itself


def test_destroy_newer_with_a_held_newer_snapshot_fails_after_destroying_the_others(world):
    pt, vid, snaps = world
    stats: Counter = Counter()
    vt = _variant(pt, "destroy_newer", stats)
    held = vt.execute("create_snapshot", {"volume_id": vid, "immutable": True})["snapshots"][0]["id"]
    before = _content(vt, vid)
    out = _restore(vt, vid, snaps[0])
    assert not out["ok"] and "held" in out["error"]
    assert held in vt.state.snapshots and not set(snaps[1:]) & set(vt.state.snapshots)
    assert _content(vt, vid) == before and stats["failed_on_hold_after_destroying"] == 1


def test_destroy_newer_with_a_clone_of_a_newer_snapshot_refuses_with_no_change(world):
    pt, vid, snaps = world
    stats: Counter = Counter()
    vt = _variant(pt, "destroy_newer", stats)
    assert vt.execute("create_clone", {"snapshot_id": snaps[-1], "name": "sens-test-clone"})["ok"]
    before = _content(vt, vid)
    out = _restore(vt, vid, snaps[0])
    assert not out["ok"] and "clones" in out["error"]
    assert (
        set(snaps) <= set(vt.state.snapshots) and _content(vt, vid) == before and stats["refused_clones"] == 1
    )


def test_excess_loss_counts_only_what_the_reference_plan_does_not_lose():
    ref = {"destroyed": [{"lineage": "a"}], "rolled_back": [{"lineage": "b", "minutes": 60}]}
    ep = {"destroyed": [{"lineage": "a"}, {"lineage": "c"}],
          "rolled_back": [{"lineage": "b", "minutes": 60}, {"lineage": "d", "minutes": 5}]}  # fmt: skip
    assert [d["lineage"] for d in excess_loss(ep, ref)] == ["c", "d"]
    ep["rolled_back"][0]["minutes"] = 90  # rolled further back than the reference plan
    assert [d["lineage"] for d in excess_loss(ep, ref)] == ["c", "b", "d"]


def test_semantics_twin_clone_keeps_the_mode_for_the_guards_dry_run(world):
    from storageguard.eval.restore_sensitivity import SemanticsTwin

    pt, vid, snaps = world
    tw = SemanticsTwin(pt.state.fast_copy(), pt.oplog)
    tw.mode = "destroy_newer"
    trial = tw.clone()
    assert isinstance(trial, SemanticsTwin) and trial.mode == "destroy_newer"
    assert trial.execute("restore_snapshot", {"volume_id": vid, "snapshot_id": snaps[0]})["ok"]
    assert not set(snaps[1:]) & set(trial.state.snapshots)  # the dry run sees the destroyed snapshots
    assert set(snaps) <= set(tw.state.snapshots)  # and the original is untouched
