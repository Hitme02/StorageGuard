"""Hypothesis property tests: capacity is never negative; log replay reproduces the state."""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from storageguard.bench.handbuilt import mini_estate
from storageguard.bench.scenario import generate_scenario
from storageguard.twin import OPS, StorageTwin, capture_public
from storageguard.twin.models import OFFSITE_VAULT

MUTATORS = sorted(n for n, s in OPS.items() if s.mutating)


@st.composite
def op_sequences(draw):
    """Random (mostly valid-looking) tool calls; picks are indexes resolved against live state."""
    n = draw(st.integers(1, 25))
    return [
        (draw(st.sampled_from(MUTATORS)), draw(st.integers(0, 10_000)), draw(st.integers(0, 10_000)),
         draw(st.booleans()), draw(st.integers(1, 3000)))
        for _ in range(n)
    ]  # fmt: skip


def _pick(coll, i):
    keys = sorted(coll)
    return keys[i % len(keys)] if keys else "missing"


def concretise(twin: StorageTwin, name: str, i: int, j: int, flag: bool, num: int) -> dict:
    s = twin.state
    vol, host, pool = _pick(s.volumes, i), _pick(s.hosts, j), _pick(s.pools, j)
    snap, link, zone = _pick(s.snapshots, i), _pick(s.replication_links, i), _pick(s.zones, i)
    table = {
        "create_volume": {"name": f"v{i}-{j}", "pool_id": pool, "size_gb": num, "thin": flag},
        "resize_volume": {"volume_id": vol, "new_size_gb": num},
        "delete_volume": {"volume_id": vol, "force": flag, "to_recycle_bin": j % 2 == 0},
        "restore_from_recycle_bin": {"volume_id": vol},
        "purge_recycle_bin": {} if flag else {"volume_id": vol},
        "migrate_volume": {"volume_id": vol, "target_pool_id": pool},
        "set_qos": {"volume_id": vol, "iops_limit": num},
        "create_snapshot": {"volume_id": vol} if flag else {"consistency_group_id": _pick(s.consistency_groups, i)},
        "delete_snapshot": {"snapshot_id": snap, "force": flag},
        "restore_snapshot": {"volume_id": s.snapshots[snap].volume_id if snap in s.snapshots else vol,
                             "snapshot_id": snap, "whole_consistency_group": flag},
        "create_clone": {"snapshot_id": snap, "name": f"c{i}-{j}"},
        "set_snapshot_immutable": {"snapshot_id": snap, "immutable": flag},
        "map_volume": {"volume_id": vol, "host_id": host},
        "unmap_volume": {"volume_id": vol, "host_id": host},
        "create_zone": {"name": f"z{i}-{j}", "fabric": "A" if flag else "B", "members": [f"w{i}", f"w{j}x"]},
        "add_zone_member": {"zone_id": zone, "wwpn": f"w{j}"},
        "remove_zone_member": {"zone_id": zone, "wwpn": (s.zones[zone].members or ["x"])[0] if zone in s.zones else "x"},
        "delete_zone": {"zone_id": zone},
        "create_replication": {"source_volume_id": vol, "target_pool_id": pool, "mode": "sync" if flag else "async"},
        "pause_replication": {"link_id": link},
        "resume_replication": {"link_id": link},
        "break_replication": {"link_id": link},
        "run_backup": {"volume_id": vol, "repository": OFFSITE_VAULT if flag else _pick(s.volumes, j)},
        "delete_backup": {"backup_id": _pick(s.backups, i)},
        "add_hot_spare": {"pool_id": pool, "disk_id": _pick(s.disks, i)},
        "remove_hot_spare": {"pool_id": pool, "disk_id": _pick(s.disks, i)},
    }  # fmt: skip
    return table[name]


def assert_invariants(twin: StorageTwin) -> None:
    s = twin.state
    for pid, p in s.pools.items():
        used = s.pool_used_gb(pid)
        assert 0 <= used <= p.physical_gb, (pid, used)
        assert s.pool_provisioned_gb(pid) >= 0
        assert set(p.hot_spare_ids) <= set(s.disks)
    for v in s.volumes.values():
        assert 0 <= v.used_gb and v.provisioned_gb > 0 and v.pool_id in s.pools
    for snap in s.snapshots.values():
        assert snap.volume_id in s.volumes and snap.size_gb >= 0
    for m in s.mappings.values():
        assert m.volume_id in s.volumes and m.host_id in s.hosts
    for link in s.replication_links.values():
        assert link.source_volume_id in s.volumes and link.target_volume_id in s.volumes
    for v in s.volumes.values():
        if v.parent_snapshot_id:
            assert v.parent_snapshot_id in s.snapshots
    for b in s.backups.values():
        assert b.repository == OFFSITE_VAULT or b.repository in s.volumes
    for row in capture_public(twin).pools.values():
        assert row["free_gb"] >= 0 and row["used_gb"] >= 0


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(op_sequences())
def test_capacity_never_negative_and_references_stay_valid(seq):
    twin = mini_estate()[0].twin
    for name, i, j, flag, num in seq:
        twin.execute(name, concretise(twin, name, i, j, flag, num))
        assert_invariants(twin)


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(op_sequences())
def test_replaying_the_log_reproduces_the_state(seq):
    twin = mini_estate()[0].twin
    initial = twin.state.fast_copy()
    for name, i, j, flag, num in seq:
        twin.execute(name, concretise(twin, name, i, j, flag, num))
    replayed = StorageTwin.replay(initial, twin.oplog)
    assert replayed.state == twin.state
    assert replayed.state.content_hash() == twin.state.content_hash()
    assert StorageTwin.from_json(twin.to_json()).state == twin.state


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(0, 10_000), op_sequences())
def test_generated_scenarios_hold_invariants(seed, seq):
    twin = generate_scenario(seed).twin
    assert_invariants(twin)
    assert 20 <= len(twin.state.volumes) <= 80
    for name, i, j, flag, num in seq:
        twin.execute(name, concretise(twin, name, i, j, flag, num))
        assert_invariants(twin)
