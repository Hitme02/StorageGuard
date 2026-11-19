"""Post-hoc variant P1: the newest-point-in-time-copy finding (off in every reported B4 result)."""

from __future__ import annotations

from storageguard.bench.splits import load_bench, task_twin
from storageguard.eval.baselines import ABLATIONS, LABELS
from storageguard.eval.posthoc import CODE, p1_config
from storageguard.guard.analyzers import Action
from storageguard.guard.config import GuardConfig
from storageguard.guard.policy import StorageGuard
from storageguard.guard.reversibility import IRREVERSIBLE_CODES
from storageguard.twin.public import to_public


def _volume_with_snapshots():
    for task in load_bench(split="dev"):
        twin = task_twin(task)
        by_vol: dict[str, list[dict]] = {}
        for s in twin.state.snapshots.values():
            p = to_public("snapshots", s)
            if not p["immutable"] and p["scan_status"] == "clean":
                by_vol.setdefault(p["volume_id"], []).append(p)
        for _vid, snaps in sorted(by_vol.items()):
            if len(snaps) >= 3 and not any(v.parent_snapshot_id in {x["id"] for x in snaps} for v in twin.state.volumes.values()):
                snaps.sort(key=lambda x: x["created_at"])
                if not _newer_backup(twin, snaps):
                    return twin, snaps
    raise AssertionError("no volume with three clean snapshots in the dev split")


def _codes(cfg: GuardConfig, twin, sid: str) -> set[str]:
    a = StorageGuard(cfg).assess(twin, [Action("delete_snapshot", {"snapshot_id": sid})])
    return {f.code for f in a.findings}


def test_off_by_default_and_in_b4():
    assert GuardConfig().pit_check is False and GuardConfig.load().pit_check is False
    assert "pit_check" not in ABLATIONS.get("storageguard", {})
    assert ABLATIONS["p1_pit_check"] == {"pit_check": True} and "post-hoc" in LABELS["p1_pit_check"]
    twin, snaps = _volume_with_snapshots()
    assert CODE not in _codes(GuardConfig.load(), twin, snaps[-1]["id"])


def test_fires_on_the_newest_copy_only():
    twin, snaps = _volume_with_snapshots()
    cfg = p1_config(0.3)
    assert cfg.pit_check and cfg.sev(CODE) == 0.3
    newest, oldest = snaps[-1]["id"], snaps[0]["id"]
    # the volume itself is newer than any snapshot, so the existing recovery-point check stays silent;
    # P1 looks at point-in-time copies only
    assert CODE in _codes(cfg, twin, newest)
    assert CODE not in _codes(cfg, twin, oldest)
    assert CODE in IRREVERSIBLE_CODES


def _newer_backup(twin, snaps) -> bool:
    """A backup newer than the newest snapshot is also a point-in-time copy (P1 would rightly stay silent)."""
    vid = snaps[-1]["volume_id"]
    backups = [to_public("backups", b) for b in twin.state.backups.values()]
    return any(b["source_volume_id"] == vid and b["created_at"] > snaps[-1]["created_at"] for b in backups)
