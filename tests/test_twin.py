"""Phase 1: storage twin entities, operations, realism rules, public API, log, clone, JSON."""

import json

from storageguard.twin import OPS, StorageTwin, capture_public
from storageguard.twin.models import INTERNAL_FIELDS, OFFSITE_VAULT
from storageguard.twin.public import PUBLIC_MODELS, iso, parse_iso


def ok(res):
    assert res["ok"], res
    return res


def err(res, text=""):
    assert not res["ok"], res
    assert text in res["error"], res
    return res


# ---------------- volumes ----------------
def test_create_volume_thin(twin, ids):
    r = ok(twin.execute("create_volume", {"name": "new-vol", "pool_id": ids.pool1, "size_gb": 500, "app_id": ids.app_erp}))
    v = twin.state.volumes[r["volume"]["id"]]
    assert v.thin and v.used_gb == 0 and v.role == "primary"


def test_create_volume_without_app_is_scratch(twin, ids):
    r = ok(twin.execute("create_volume", {"name": "x", "pool_id": ids.pool1, "size_gb": 10}))
    assert r["volume"]["role"] == "scratch"


def test_create_volume_duplicate_name_rejected(twin, ids):
    err(twin.execute("create_volume", {"name": "dev-web-01", "pool_id": ids.pool1, "size_gb": 10}), "already in use")


def test_create_thick_volume_needs_physical_space(twin, ids):
    err(twin.execute("create_volume", {"name": "big", "pool_id": ids.pool2, "size_gb": 999999, "thin": False}), "insufficient")


def test_thin_volume_can_overcommit(twin, ids):
    ok(twin.execute("create_volume", {"name": "big", "pool_id": ids.pool2, "size_gb": 999999}))
    assert twin.state.pool_overcommit(ids.pool2) > twin.state.pools[ids.pool2].overcommit_limit


def test_resize_expand_and_shrink_floor(twin, ids):
    ok(twin.execute("resize_volume", {"volume_id": ids.dev, "new_size_gb": 800}))
    assert twin.state.volumes[ids.dev].provisioned_gb == 800
    err(twin.execute("resize_volume", {"volume_id": ids.dev, "new_size_gb": 100}), "shrink")


def test_delete_volume_blocked_by_mapping_snapshots_replication(twin, ids):
    e = err(twin.execute("delete_volume", {"volume_id": ids.data}))["error"]
    assert "mapped" in e and "snapshot" in e and "replication" in e


def test_delete_volume_force_cascades(twin, ids):
    r = ok(twin.execute("delete_volume", {"volume_id": ids.data, "force": True}))
    s = twin.state
    assert ids.data not in s.volumes
    assert not [x for x in s.snapshots.values() if x.volume_id == ids.data]
    assert ids.link not in s.replication_links
    assert ids.clone not in s.volumes  # clone of a cascaded snapshot goes too
    assert ids.clone in r["deleted_clones"] and ids.link in r["broken_links"]
    assert ids.data not in s.consistency_groups[ids.cg].volume_ids


def test_delete_plain_volume(twin, ids):
    ok(twin.execute("delete_volume", {"volume_id": ids.scratch}))
    assert ids.scratch not in twin.state.volumes


def test_delete_backup_repository_deletes_backups(twin, ids):
    r = ok(twin.execute("delete_volume", {"volume_id": ids.repo}))
    assert set(r["deleted_backups"]) == set(ids.crm_backups)
    assert not any(b.repository == ids.repo for b in twin.state.backups.values())


def test_recycle_bin_keeps_capacity_until_purged(twin, ids):
    used0 = twin.state.pool_used_gb(ids.pool2)
    ok(twin.execute("delete_volume", {"volume_id": ids.scratch, "to_recycle_bin": True}))
    assert twin.state.volumes[ids.scratch].in_recycle_bin
    assert twin.state.pool_used_gb(ids.pool2) == used0
    ok(twin.execute("purge_recycle_bin", {"volume_id": ids.scratch}))
    assert twin.state.pool_used_gb(ids.pool2) == used0 - 150


def test_recycle_bin_restore(twin, ids):
    ok(twin.execute("delete_volume", {"volume_id": ids.dev, "force": True, "to_recycle_bin": True}))
    assert not [m for m in twin.state.mappings.values() if m.volume_id == ids.dev]
    assert twin.state.snapshots[ids.dev_snap]  # snapshots survive in the bin
    ok(twin.execute("restore_from_recycle_bin", {"volume_id": ids.dev}))
    assert not twin.state.volumes[ids.dev].in_recycle_bin


def test_recycle_bin_pauses_replication(twin, ids):
    ok(twin.execute("delete_volume", {"volume_id": ids.replica, "force": True, "to_recycle_bin": True}))
    assert twin.state.replication_links[ids.link].state == "paused"


def test_purge_all(twin, ids):
    ok(twin.execute("delete_volume", {"volume_id": ids.scratch, "to_recycle_bin": True}))
    r = ok(twin.execute("purge_recycle_bin", {}))
    assert r["purged"] == [ids.scratch]


def test_binned_volume_cannot_be_mapped_or_deleted_again(twin, ids):
    ok(twin.execute("delete_volume", {"volume_id": ids.scratch, "to_recycle_bin": True}))
    err(twin.execute("map_volume", {"volume_id": ids.scratch, "host_id": ids.h1}), "recycle bin")
    err(twin.execute("delete_volume", {"volume_id": ids.scratch}), "purge_recycle_bin")


def test_migrate_volume_moves_capacity(twin, ids):
    s = twin.state
    u1, u2 = s.pool_used_gb(ids.pool1), s.pool_used_gb(ids.pool2)
    r = ok(twin.execute("migrate_volume", {"volume_id": ids.dev, "target_pool_id": ids.pool1}))
    assert s.pool_used_gb(ids.pool1) == u1 + r["moved_gb"] and s.pool_used_gb(ids.pool2) == u2 - r["moved_gb"]
    err(twin.execute("migrate_volume", {"volume_id": ids.dev, "target_pool_id": ids.pool_dr}), "same array")


def test_set_qos(twin, ids):
    ok(twin.execute("set_qos", {"volume_id": ids.dev, "iops_limit": 500}))
    assert twin.state.volumes[ids.dev].qos_iops_limit == 500
    err(twin.execute("set_qos", {"volume_id": ids.dev, "iops_limit": 0}), "positive")


# ---------------- snapshots & clones ----------------
def test_create_snapshot_volume(twin, ids):
    r = ok(twin.execute("create_snapshot", {"volume_id": ids.dev}))
    snap = twin.state.snapshots[r["snapshots"][0]["id"]]
    assert snap.point_in_time == twin.state.volumes[ids.dev].content_time and snap.true_clean


def test_create_snapshot_consistency_group(twin, ids):
    r = ok(twin.execute("create_snapshot", {"consistency_group_id": ids.cg}))
    assert {x["volume_id"] for x in r["snapshots"]} == {ids.data, ids.redo}
    assert len({x["created_at"] for x in r["snapshots"]}) == 1


def test_create_snapshot_needs_exactly_one_target(twin, ids):
    err(twin.execute("create_snapshot", {}), "exactly one")
    err(twin.execute("create_snapshot", {"volume_id": ids.dev, "consistency_group_id": ids.cg}), "exactly one")


def test_delete_snapshot_with_clone_needs_force(twin, ids):
    err(twin.execute("delete_snapshot", {"snapshot_id": ids.clone_parent}), "dependent clone")
    r = ok(twin.execute("delete_snapshot", {"snapshot_id": ids.clone_parent, "force": True}))
    assert r["deleted_clones"] == [ids.clone] and ids.clone not in twin.state.volumes


def test_immutable_snapshot_cannot_be_deleted_until_unlocked(twin, ids):
    ok(twin.execute("set_snapshot_immutable", {"snapshot_id": ids.dev_snap, "immutable": True}))
    err(twin.execute("delete_snapshot", {"snapshot_id": ids.dev_snap}), "immutable")
    err(twin.execute("delete_volume", {"volume_id": ids.dev, "force": True}), "immutable")
    ok(twin.execute("set_snapshot_immutable", {"snapshot_id": ids.dev_snap, "immutable": False}))
    ok(twin.execute("delete_snapshot", {"snapshot_id": ids.dev_snap}))


def test_restore_snapshot_rolls_content_back(twin, ids):
    snap = twin.state.snapshots[ids.dev_snap]
    ok(twin.execute("restore_snapshot", {"volume_id": ids.dev, "snapshot_id": ids.dev_snap}))
    assert twin.state.volumes[ids.dev].content_time == snap.point_in_time


def test_restore_snapshot_wrong_volume(twin, ids):
    err(twin.execute("restore_snapshot", {"volume_id": ids.crm, "snapshot_id": ids.dev_snap}), "does not belong")


def test_restore_whole_consistency_group(twin, ids):
    s_data, s_redo = ids.cg_sets[1]
    r = ok(twin.execute("restore_snapshot", {"volume_id": ids.data, "snapshot_id": s_data, "whole_consistency_group": True}))
    assert len(r["restored"]) == 2
    st = twin.state
    assert st.volumes[ids.data].content_time == st.volumes[ids.redo].content_time == st.snapshots[s_redo].point_in_time


def test_restore_propagates_to_sync_replica(twin, ids):
    ok(twin.execute("restore_snapshot", {"volume_id": ids.data, "snapshot_id": ids.cg_sets[1][0]}))
    assert twin.state.volumes[ids.replica].content_time == twin.state.volumes[ids.data].content_time


def test_create_clone(twin, ids):
    r = ok(twin.execute("create_clone", {"snapshot_id": ids.dev_snap, "name": "dev-web-copy"}))
    v = twin.state.volumes[r["volume"]["id"]]
    assert v.role == "clone" and v.parent_snapshot_id == ids.dev_snap and v.pool_id == ids.pool2
    assert v.origin_data_id == twin.state.volumes[ids.dev].data_id


# ---------------- access ----------------
def test_map_unmap(twin, ids):
    ok(twin.execute("map_volume", {"volume_id": ids.scratch, "host_id": ids.h3}))
    assert twin.state.reachable(ids.h3, ids.scratch)
    err(twin.execute("map_volume", {"volume_id": ids.scratch, "host_id": ids.h3}), "already mapped")
    ok(twin.execute("unmap_volume", {"volume_id": ids.scratch, "host_id": ids.h3}))
    assert not twin.state.reachable(ids.h3, ids.scratch)
    err(twin.execute("unmap_volume", {"volume_id": ids.scratch, "host_id": ids.h3}), "not mapped")


def test_lun_ids_unique_per_host(twin, ids):
    r = ok(twin.execute("map_volume", {"volume_id": ids.scratch, "host_id": ids.h3}))
    assert r["lun_id"] not in (1, 2)
    err(twin.execute("map_volume", {"volume_id": ids.crm, "host_id": ids.h3, "lun_id": 1}), "in use")


def test_reachability_needs_zone_and_mapping(estate):
    b, ids = estate
    h = b.host("new-host", ids.cl_prod)  # no zones yet
    b.map(ids.crm, [h.id])
    assert not b.s.reachable(h.id, ids.crm)
    arr = b.s.arrays[ids.arr_p]
    ok(b.twin.execute("create_zone", {"name": "z_new_A", "fabric": "A", "members": [h.initiators[0], arr.target_ports[0]]}))
    assert b.s.reachable(h.id, ids.crm)


def test_zone_member_ops(twin, ids):
    z = next(iter(twin.state.zones.values()))
    ok(twin.execute("add_zone_member", {"zone_id": z.id, "wwpn": "aa:bb"}))
    err(twin.execute("add_zone_member", {"zone_id": z.id, "wwpn": "aa:bb"}), "already")
    ok(twin.execute("remove_zone_member", {"zone_id": z.id, "wwpn": "aa:bb"}))
    err(twin.execute("remove_zone_member", {"zone_id": z.id, "wwpn": "aa:bb"}), "not a member")
    ok(twin.execute("delete_zone", {"zone_id": z.id}))
    assert z.id not in twin.state.zones


def test_removing_all_zones_cuts_reachability(twin, ids):
    assert twin.state.reachable(ids.h3, ids.dev)
    for z in [z for z in twin.state.zones.values() if set(z.members) & set(twin.state.hosts[ids.h3].initiators)]:
        ok(twin.execute("delete_zone", {"zone_id": z.id}))
    assert not twin.state.reachable(ids.h3, ids.dev)


def test_create_zone_validation(twin):
    err(twin.execute("create_zone", {"name": "z", "fabric": "A", "members": ["x"]}), "at least two")
    err(twin.execute("create_zone", {"name": "z", "fabric": "C", "members": ["x", "y"]}), "invalid arguments")


# ---------------- replication & backup ----------------
def test_replication_lifecycle(twin, ids):
    ok(twin.execute("pause_replication", {"link_id": ids.link}))
    err(twin.execute("pause_replication", {"link_id": ids.link}), "not active")
    ok(twin.execute("resume_replication", {"link_id": ids.link}))
    ok(twin.execute("break_replication", {"link_id": ids.link}))
    err(twin.execute("resume_replication", {"link_id": ids.link}), "only a paused")
    assert twin.state.replication_links[ids.link].state == "broken"


def test_create_replication(twin, ids):
    r = ok(twin.execute("create_replication", {"source_volume_id": ids.crm, "target_pool_id": ids.pool_dr, "mode": "async"}))
    link = twin.state.replication_links[r["replication_link"]["id"]]
    tgt = twin.state.volumes[link.target_volume_id]
    assert tgt.role == "replica_target" and tgt.data_id == twin.state.volumes[ids.crm].data_id
    err(twin.execute("create_replication", {"source_volume_id": ids.dev, "target_pool_id": ids.pool1}), "different array")
    err(twin.execute("create_replication", {"source_volume_id": ids.crm, "target_pool_id": ids.pool_dr}), "already replicated")


def test_paused_replica_keeps_old_content(twin, ids):
    ok(twin.execute("pause_replication", {"link_id": ids.link}))
    before = twin.state.volumes[ids.replica].content_time
    ok(twin.execute("restore_snapshot", {"volume_id": ids.data, "snapshot_id": ids.cg_sets[1][0]}))
    assert twin.state.volumes[ids.replica].content_time == before


def test_run_backup_offsite_and_repo(twin, ids):
    r = ok(twin.execute("run_backup", {"volume_id": ids.dev}))
    assert r["backup"]["repository"] == OFFSITE_VAULT
    used = twin.state.volumes[ids.repo].used_gb
    r = ok(twin.execute("run_backup", {"volume_id": ids.dev, "repository": ids.repo}))
    assert twin.state.volumes[ids.repo].used_gb == used + r["backup"]["size_gb"]


def test_delete_backup_and_immutability(twin, ids):
    bid = ids.crm_backups[0]
    twin.state.backups[bid].immutable = True
    err(twin.execute("delete_backup", {"backup_id": bid}), "immutable")
    ok(twin.execute("delete_backup", {"backup_id": ids.crm_backups[1]}))
    assert ids.crm_backups[1] not in twin.state.backups


# ---------------- hot spares ----------------
def test_hot_spare_move_between_pools(twin, ids):
    ok(twin.execute("remove_hot_spare", {"pool_id": ids.pool1, "disk_id": ids.spare1}))
    assert twin.state.disks[ids.spare1].role == "unassigned"
    ok(twin.execute("add_hot_spare", {"pool_id": ids.pool2, "disk_id": ids.spare1}))
    assert ids.spare1 in twin.state.pools[ids.pool2].hot_spare_ids
    err(twin.execute("add_hot_spare", {"pool_id": ids.pool2, "disk_id": ids.spare1}), "not an unassigned")


def test_remove_hot_spare_from_rebuilding_pool_is_allowed_by_twin(estate):
    b, ids = estate
    b.fail_disk(ids.pool1)
    p = b.s.pools[ids.pool1]
    assert p.state == "rebuilding" and len(p.hot_spare_ids) == 1
    ok(b.twin.execute("remove_hot_spare", {"pool_id": ids.pool1, "disk_id": p.hot_spare_ids[0]}))
    assert b.s.pools[ids.pool1].hot_spare_ids == []


def test_fail_disk_without_spare_degrades(estate):
    b, ids = estate
    b.fail_disk(ids.pool2)
    b.fail_disk(ids.pool2)
    assert b.s.pools[ids.pool2].state == "degraded"


# ---------------- capacity ----------------
def test_capacity_report_accounts_volumes_and_snapshots(twin, ids):
    rep = ok(twin.execute("get_capacity_report"))
    row = next(r for r in rep["pools"] if r["pool_id"] == ids.pool2)
    s = twin.state
    expect = sum(v.used_gb for v in s.volumes.values() if v.pool_id == ids.pool2) + s.pool_snapshot_gb(ids.pool2)
    assert row["used_gb"] == expect and row["free_gb"] == row["physical_gb"] - expect
    assert row["overcommit_ratio"] == round(s.pool_provisioned_gb(ids.pool2) / row["physical_gb"], 2)


def test_thick_volume_consumes_provisioned(twin, ids):
    u = twin.state.pool_used_gb(ids.pool1)
    ok(twin.execute("create_volume", {"name": "thick", "pool_id": ids.pool1, "size_gb": 400, "thin": False}))
    assert twin.state.pool_used_gb(ids.pool1) == u + 400


# ---------------- public boundary ----------------
def test_public_views_have_no_internal_fields(twin):
    ps = capture_public(twin)
    blob = json.dumps({k: getattr(ps, k) for k in ps.ENTITY_KINDS} | {"m": ps.mappings, "a": ps.alerts})
    for fields in INTERNAL_FIELDS.values():
        for f in fields:
            assert f'"{f}"' not in blob


def test_every_tool_output_is_free_of_internal_fields(twin, ids):
    args = {"get_pool": {"pool_id": ids.pool1}, "get_volume": {"volume_id": ids.data}}
    for name, spec in OPS.items():
        if spec.mutating:
            continue
        blob = json.dumps(ok(twin.execute(name, args.get(name, {}))))
        assert not any(f'"{f}"' in blob for fs in INTERNAL_FIELDS.values() for f in fs), name


def test_public_rows_validate_against_public_models(twin):
    ps = capture_public(twin)
    for kind in ("volumes", "snapshots", "backups", "hosts", "zones", "replication_links", "apps"):
        for row in getattr(ps, kind).values():
            PUBLIC_MODELS[kind].model_validate(row)


def test_mutation_results_do_not_leak(twin, ids):
    r = ok(twin.execute("create_snapshot", {"volume_id": ids.data}))
    assert "true_clean" not in json.dumps(r) and "data_id" not in json.dumps(r)


def test_iso_roundtrip():
    assert parse_iso(iso(238215)) == 238215
    assert iso(0) == "2026-01-01T00:00Z" and iso(None) is None


def test_public_reachability_matches_internal(twin):
    ps = capture_public(twin)
    s = twin.state
    internal = {(m.host_id, m.volume_id) for m in s.mappings.values() if s.reachable(m.host_id, m.volume_id)}
    assert ps.reachable_pairs == internal


# ---------------- execution, log, clone, JSON ----------------
def test_unknown_tool_and_bad_args_are_malformed(twin, ids):
    assert twin.execute("format_everything", {})["malformed"]
    assert twin.execute("delete_volume", {"volume": ids.dev})["malformed"]
    assert twin.execute("resize_volume", {"volume_id": ids.dev, "new_size_gb": "lots"})["malformed"]


def test_args_are_coerced(twin, ids):
    ok(twin.execute("resize_volume", {"volume_id": ids.dev, "new_size_gb": "900"}))
    assert twin.state.volumes[ids.dev].provisioned_gb == 900


def test_name_resolution(twin, ids):
    args = twin.resolve_args("get_volume", {"volume_id": "dev-web-01"})
    assert args["volume_id"] == ids.dev
    assert twin.resolve_args("get_volume", {"volume_id": "nope"})["volume_id"] == "nope"


def test_clock_advances_only_on_successful_mutation(twin, ids):
    t = twin.now
    twin.execute("list_volumes")
    twin.execute("delete_volume", {"volume_id": ids.data})  # fails
    assert twin.now == t
    twin.execute("create_snapshot", {"volume_id": ids.dev})
    assert twin.now == t + 1


def test_failed_operation_rolls_back_atomically(twin, ids):
    twin.state.snapshots[ids.cg_sets[2][1]].immutable = True
    h = twin.state.content_hash()
    # purge-all hits an immutable snapshot only after other work could have started
    ok(twin.execute("delete_volume", {"volume_id": ids.scratch, "to_recycle_bin": True}))
    ok(twin.execute("delete_volume", {"volume_id": ids.redo, "force": True, "to_recycle_bin": True}))
    h = twin.state.content_hash()
    err(twin.execute("purge_recycle_bin", {}), "immutable")
    assert twin.state.content_hash() == h


def test_oplog_records_diffs(twin, ids):
    twin.execute("delete_volume", {"volume_id": ids.repo})
    e = twin.oplog.entries[-1]
    assert e.ok and ids.repo in e.diff["deleted"]["volumes"]
    assert set(e.diff["deleted"]["backups"]) == set(ids.crm_backups)
    twin.execute("resize_volume", {"volume_id": ids.dev, "new_size_gb": 700})
    assert twin.oplog.entries[-1].diff["changed"]["volumes"][ids.dev]["provisioned_gb"] == [500, 700]
    twin.execute("delete_volume", {"volume_id": "nope"})
    assert not twin.oplog.entries[-1].ok and twin.oplog.entries[-1].diff == {}


def test_clone_is_independent(twin, ids):
    c = twin.clone()
    c.execute("delete_volume", {"volume_id": ids.data, "force": True})
    c.execute("add_zone_member", {"zone_id": next(iter(c.state.zones)), "wwpn": "zz"})
    assert ids.data in twin.state.volumes and len(twin.oplog) == 0
    assert all("zz" not in z.members for z in twin.state.zones.values())
    assert twin.state.consistency_groups[ids.cg].volume_ids == [ids.data, ids.redo]


def test_json_roundtrip(twin, ids):
    twin.execute("create_snapshot", {"volume_id": ids.dev})
    twin2 = StorageTwin.from_json(twin.to_json())
    assert twin2.state.content_hash() == twin.state.content_hash()
    assert len(twin2.oplog) == len(twin.oplog)
    assert twin2.state == twin.state


def test_replay_reproduces_state(twin, ids):
    initial = twin.state.fast_copy()
    for op_name, args in [
        ("create_volume", {"name": "n1", "pool_id": ids.pool1, "size_gb": 100}),
        ("delete_volume", {"volume_id": ids.data}),  # fails
        ("delete_volume", {"volume_id": ids.data, "force": True}),
        ("break_replication", {"link_id": ids.link}),  # fails: link gone
        ("run_backup", {"volume_id": ids.crm, "repository": ids.repo}),
    ]:
        twin.execute(op_name, args)
    replayed = StorageTwin.replay(initial, twin.oplog)
    assert replayed.state.content_hash() == twin.state.content_hash()
