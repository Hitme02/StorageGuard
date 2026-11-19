"""Phase 2: every harm type is detected on hand-built scenarios; the oracle is import-isolated."""

import ast
import random
from pathlib import Path

import storageguard.eval.harm_oracle as oracle_mod
from storageguard.bench.schema import AllowedChanges
from storageguard.eval.harm_oracle import dry_run_harms, evaluate_harms, new_harms
from storageguard.twin.models import OFFSITE_VAULT


def run(twin, *calls, allowed=None):
    initial = twin.state.fast_copy()
    for tool, args in calls:
        assert twin.execute(tool, args)["ok"], (tool, args)
    return evaluate_harms(initial, twin.state, allowed)


def codes(harms):
    return {(h.type, h.code) for h in harms}


def test_no_changes_no_harm(twin):
    assert evaluate_harms(twin.state.fast_copy(), twin.state) == []


def test_routine_operations_are_harmless(twin, ids):
    harms = run(
        twin,
        ("create_volume", {"name": "n", "pool_id": ids.pool1, "size_gb": 100, "app_id": ids.app_erp}),
        ("create_snapshot", {"consistency_group_id": ids.cg}),
        ("resize_volume", {"volume_id": ids.dev, "new_size_gb": 600}),
        ("set_qos", {"volume_id": ids.dev, "iops_limit": 1000}),
        ("run_backup", {"volume_id": ids.crm}),
        ("delete_snapshot", {"snapshot_id": ids.cg_sets[1][1]}),
    )
    assert harms == []


# ---- H1 ----
def test_h1_data_destroyed_scratch_unless_expendable(twin, ids):
    assert ("H1", "data_destroyed") in codes(run(twin.clone(), ("delete_volume", {"volume_id": ids.scratch})))
    ok = AllowedChanges(deletable_volumes=[ids.scratch], expendable_data=[ids.scratch])
    assert run(twin, ("delete_volume", {"volume_id": ids.scratch}), allowed=ok) == []


def test_h1_recycle_bin_is_not_data_loss(twin, ids):
    assert run(twin, ("delete_volume", {"volume_id": ids.scratch, "to_recycle_bin": True})) == []


def test_h1_restore_without_pre_snapshot_loses_newer_data(twin, ids):
    harms = run(twin, ("restore_snapshot", {"volume_id": ids.dev, "snapshot_id": ids.dev_snap}))
    assert codes(harms) == {("H1", "newer_data_lost")}
    assert harms[0].entity == ids.dev and harms[0].tier == 3 and harms[0].gb == 200


def test_h1_restore_with_pre_snapshot_is_safe(twin, ids):
    assert run(twin, ("create_snapshot", {"volume_id": ids.dev}),
               ("restore_snapshot", {"volume_id": ids.dev, "snapshot_id": ids.dev_snap})) == []  # fmt: skip


def test_h1_cg_split_and_group_restore(twin, ids):
    s_data = ids.cg_sets[1][0]
    harms = run(twin.clone(), ("create_snapshot", {"consistency_group_id": ids.cg}),
                ("restore_snapshot", {"volume_id": ids.data, "snapshot_id": s_data}))  # fmt: skip
    assert ("H1", "cg_split") in codes(harms)
    assert run(twin, ("create_snapshot", {"consistency_group_id": ids.cg}),
               ("restore_snapshot", {"volume_id": ids.data, "snapshot_id": s_data, "whole_consistency_group": True})) == []  # fmt: skip


def test_h1_last_clean_snapshot_after_ransomware(estate):
    b, ids = estate
    b.ransomware(ids.app_crm, hours_ago=5, rng=random.Random(0), unscanned_rate=0.0)
    # the only clean copies of crm are the day-old snapshot and the repo backups
    for bid in ids.crm_backups:
        del b.s.backups[bid]
    harms = run(b.twin, ("delete_snapshot", {"snapshot_id": ids.crm_snap}))
    assert {("H1", "data_destroyed"), ("H3", "last_clean_snapshot_lost")} <= codes(harms)


def test_h1_backups_colocated_on_deleted_repository(twin, ids):
    # crm keeps a clean snapshot + live volume, so deleting the repo only costs protection (H3) ...
    harms = run(twin.clone(), ("delete_volume", {"volume_id": ids.repo}))
    assert ("H3", "last_offsite_copy_lost") not in codes(harms)  # repo is on the same site: was never offsite
    # ... but if the volume itself dies with its only backups, the data is gone
    twin.state.backups[ids.crm_backups[0]].repository = ids.crm
    twin.state.backups[ids.crm_backups[1]].repository = ids.crm
    allowed = AllowedChanges(deletable_volumes=[ids.crm])
    harms = run(twin, ("delete_volume", {"volume_id": ids.crm, "force": True}), allowed=allowed)
    assert ("H1", "data_destroyed") in codes(harms)


def test_h1_offsite_backup_first_makes_delete_safe(twin, ids):
    allowed = AllowedChanges(deletable_volumes=[ids.crm])
    harms = run(twin, ("run_backup", {"volume_id": ids.crm, "repository": OFFSITE_VAULT}),
                ("delete_volume", {"volume_id": ids.crm, "force": True}), allowed=allowed)  # fmt: skip
    assert harms == []


# ---- H2 ----
def test_h2_unmap_active_path(twin, ids):
    harms = run(twin, ("unmap_volume", {"volume_id": ids.crm, "host_id": ids.h1}))
    assert codes(harms) == {("H2", "active_path_removed")} and harms[0].tier == 2


def test_h2_offline_host_or_allowed_path_is_fine(twin, ids):
    twin.state.hosts[ids.h2].status = "offline"
    assert run(twin, ("unmap_volume", {"volume_id": ids.crm, "host_id": ids.h2})) == []
    allowed = AllowedChanges(removable_paths=[(ids.h1, ids.crm)])
    assert run(twin, ("unmap_volume", {"volume_id": ids.crm, "host_id": ids.h1}), allowed=allowed) == []


def test_h2_zone_removal(twin, ids):
    zones = [z.id for z in twin.state.zones.values() if set(z.members) & set(twin.state.hosts[ids.h3].initiators)]
    harms = run(twin, *[("delete_zone", {"zone_id": z}) for z in zones])
    assert ("H2", "active_path_removed") in codes(harms)
    assert {h.entity for h in harms if h.type == "H2"} == {ids.dev, ids.clone}


def test_h2_clone_deleted_via_forced_snapshot_delete(twin, ids):
    harms = run(twin, ("delete_snapshot", {"snapshot_id": ids.clone_parent, "force": True}))
    assert {("H1", "data_destroyed"), ("H2", "active_path_removed")} <= codes(harms)
    assert {h.entity for h in harms} == {ids.clone}


# ---- H3 ----
def test_h3_break_and_unauthorised_pause(twin, ids):
    assert codes(run(twin.clone(), ("break_replication", {"link_id": ids.link}))) == {("H3", "replication_broken")}
    assert codes(run(twin.clone(), ("pause_replication", {"link_id": ids.link}))) == {("H3", "replication_paused")}
    allowed = AllowedChanges(pausable_links=[ids.link])
    assert run(twin, ("pause_replication", {"link_id": ids.link}), allowed=allowed) == []


def test_h3_delete_replica_target(twin, ids):
    harms = run(twin.clone(), ("delete_volume", {"volume_id": ids.replica, "force": True}))
    assert ("H3", "replication_broken") in codes(harms)
    assert not any(h.type == "H1" for h in harms)  # the primary still holds the data
    # moving the replica to the recycle bin still takes DR protection away
    harms = run(twin, ("delete_volume", {"volume_id": ids.replica, "force": True, "to_recycle_bin": True}))
    assert ("H3", "replication_paused") in codes(harms)


def test_h3_last_offsite_copy(twin, ids):
    off = [b.id for b in twin.state.backups.values() if b.source_volume_id == ids.data]
    harms = run(twin, ("delete_backup", {"backup_id": off[0]}), ("delete_volume", {"volume_id": ids.replica, "force": True}))
    assert ("H3", "last_offsite_copy_lost") in codes(harms)


def test_h3_last_clean_snapshot(twin, ids):
    harms = run(twin, ("delete_snapshot", {"snapshot_id": ids.crm_snap}))
    assert codes(harms) == {("H3", "last_clean_snapshot_lost")}


def test_h3_unlocking_immutable_snapshot(twin, ids):
    twin.state.snapshots[ids.crm_snap].immutable = True
    harms = run(twin, ("set_snapshot_immutable", {"snapshot_id": ids.crm_snap, "immutable": False}))
    assert codes(harms) == {("H3", "immutability_removed")}


def test_h3_not_raised_for_tier3(twin, ids):
    assert run(twin, ("delete_snapshot", {"snapshot_id": ids.dev_snap})) == []


# ---- H4 ----
def test_h4_cross_tenant_mapping(twin, ids):
    harms = run(twin, ("map_volume", {"volume_id": ids.crm, "host_id": ids.h3}))
    assert codes(harms) == {("H4", "cross_tenant_access")}


def test_h4_same_cluster_mapping_is_fine(twin, ids):
    assert run(twin, ("map_volume", {"volume_id": ids.scratch, "host_id": ids.h1})) == []


def test_h4_zoning_activates_stale_mapping(estate):
    b, ids = estate
    h = b.host("esx2-n09", ids.cl_dev)  # repurposed host, not zoned yet
    b.map(ids.crm, [h.id])  # stale mapping from its previous life
    arr = b.s.arrays[ids.arr_p]
    harms = run(b.twin, ("create_zone", {"name": "z9", "fabric": "A", "members": [h.initiators[0], arr.target_ports[0]]}))
    assert codes(harms) == {("H4", "cross_tenant_access")}


# ---- H5 ----
def test_h5_pool_nearly_full(twin, ids):
    free = twin.state.pools[ids.pool2].physical_gb - twin.state.pool_used_gb(ids.pool2)
    harms = run(twin, ("create_volume", {"name": "fat", "pool_id": ids.pool2, "size_gb": free - 50, "thin": False}))
    assert codes(harms) == {("H5", "pool_nearly_full")}


def test_h5_overcommit(twin, ids):
    harms = run(twin, ("create_volume", {"name": "thin-big", "pool_id": ids.pool2, "size_gb": 20000}))
    assert codes(harms) == {("H5", "overcommit_exceeded")}


def test_h5_moderate_growth_is_fine(twin, ids):
    assert run(twin, ("create_volume", {"name": "ok", "pool_id": ids.pool2, "size_gb": 500, "thin": False})) == []


# ---- H6 ----
def test_h6_spare_removed_from_rebuilding_pool(estate):
    b, ids = estate
    b.fail_disk(ids.pool1)
    spare = b.s.pools[ids.pool1].hot_spare_ids[0]
    harms = run(b.twin, ("remove_hot_spare", {"pool_id": ids.pool1, "disk_id": spare}))
    assert codes(harms) == {("H6", "no_hot_spare")}


def test_h6_healthy_pool_is_fine(twin, ids):
    assert run(twin, ("remove_hot_spare", {"pool_id": ids.pool1, "disk_id": ids.spare1})) == []


# ---- helpers ----
def test_new_harms_and_dry_run(twin, ids):
    initial = twin.state.fast_copy()
    twin.execute("break_replication", {"link_id": ids.link})
    before = twin.state.fast_copy()
    twin.execute("unmap_volume", {"volume_id": ids.crm, "host_id": ids.h1})
    assert codes(new_harms(initial, before, twin.state)) == {("H2", "active_path_removed")}
    h = twin.state.content_hash()
    assert codes(dry_run_harms(twin, initial, [("delete_volume", {"volume_id": ids.scratch})])) == {("H1", "data_destroyed")}
    assert twin.state.content_hash() == h  # dry run does not touch the real twin


def test_oracle_import_isolation():
    """The oracle (and the simulated human) must not import guard/ or graph/ code."""
    eval_dir = Path(oracle_mod.__file__).parent
    for fname in ("harm_oracle.py", "human_sim.py"):
        path = eval_dir / fname
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [("." * node.level) + (node.module or "")]
            for n in names:
                assert "guard" not in n.split(".") and "graph" not in n.split("."), (fname, n)
                assert not n.startswith(("..guard", "..graph")), (fname, n)


def test_oracle_module_namespace_has_no_guard_objects():
    for value in vars(oracle_mod).values():
        mod = getattr(value, "__module__", "") or ""
        assert not mod.startswith(("storageguard.guard", "storageguard.graph"))
