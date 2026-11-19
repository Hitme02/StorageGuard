"""Phase 3: guard decisions on hand-built trap scenarios, routine actions, rewrites, latency."""

import random
import statistics
import time

from storageguard.bench.scenario import DAY, HOUR, generate_scenario
from storageguard.eval.harm_oracle import evaluate_harms
from storageguard.guard.analyzers import Action
from storageguard.guard.config import GuardConfig
from storageguard.guard.middleware import StorageGuardMiddleware
from storageguard.guard.policy import ALLOW, BLOCK, ESCALATE, REWRITE, StorageGuard
from storageguard.twin.models import OFFSITE_VAULT

G = StorageGuard()


def decide(twin, tool, **args):
    return G.decide(twin, Action(tool, args))


def inv(d):
    return {v.invariant for v in d.assessment.violations}


def codes(d):
    return {f.code for f in d.assessment.findings}


class Yes:
    def review(self, twin, steps, original):
        return True


class No:
    def review(self, twin, steps, original):
        return False


# ---------------------------------------------------------------- routine -> ALLOW
def test_routine_actions_are_allowed(twin, ids):
    arr = twin.state.arrays[ids.arr_p]
    h1 = twin.state.hosts[ids.h1]
    routine = [
        ("create_volume", dict(name="ora-erp-data-02", pool_id=ids.pool1, size_gb=500, app_id=ids.app_erp)),
        ("map_volume", dict(volume_id=ids.scratch, host_id=ids.h1)),
        ("create_snapshot", dict(volume_id=ids.data)),
        ("create_snapshot", dict(consistency_group_id=ids.cg)),
        ("resize_volume", dict(volume_id=ids.dev, new_size_gb=700)),
        ("set_qos", dict(volume_id=ids.dev, iops_limit=2000)),
        ("create_zone", dict(name="z_new", fabric="A", members=[h1.initiators[0], arr.target_ports[1]])),
        ("run_backup", dict(volume_id=ids.crm)),
        ("create_clone", dict(snapshot_id=ids.dev_snap, name="dev-copy")),
        ("set_snapshot_immutable", dict(snapshot_id=ids.dev_snap, immutable=True)),
        ("create_replication", dict(source_volume_id=ids.crm, target_pool_id=ids.pool_dr)),
        ("delete_snapshot", dict(snapshot_id=ids.cg_sets[1][1])),  # redundant snapshot
        ("delete_volume", dict(volume_id=ids.scratch)),  # unowned scratch volume
        ("migrate_volume", dict(volume_id=ids.dev, target_pool_id=ids.pool1)),
    ]
    for tool, args in routine:
        d = G.decide(twin, Action(tool, args))
        assert d.kind == ALLOW, (tool, d.assessment.risk, codes(d))
        assert d.assessment.error is None, (tool, d.assessment.error)


def test_failing_action_passes_through(twin, ids):
    d = decide(twin, "delete_volume", volume_id=ids.data)  # blocked by the array itself (no force)
    assert d.kind == ALLOW and d.assessment.error


# ---------------------------------------------------------------- one scenario per trap type
def test_t1_idle_replica_target(twin, ids):
    d = decide(twin, "delete_volume", volume_id=ids.replica, force=True)
    assert d.kind == BLOCK and "I6" in inv(d)
    assert {"replication_broken"} <= codes(d)
    paths = {i.entity: i.path for i in d.assessment.impacted}
    assert ids.app_erp in paths and "replicates_to" in paths[ids.data]


def test_t2_last_clean_snapshot_after_ransomware(estate):
    b, ids = estate
    b.ransomware(ids.app_erp, hours_ago=5, rng=random.Random(1), unscanned_rate=0.0)
    for bk in [x.id for x in b.s.backups.values() if x.source_volume_id == ids.data]:
        del b.s.backups[bk]
    old, mid, enc = (s[0] for s in ids.cg_sets)  # 2 days (clean), 1 day (clean), 2 h (encrypted)
    assert decide(b.twin, "delete_snapshot", snapshot_id=enc).kind == ALLOW
    # the 2-day snapshot has a QA clone; the newest clean one (1 day) is what matters
    d = decide(b.twin, "delete_snapshot", snapshot_id=mid)
    assert d.kind in (BLOCK, ESCALATE) and "recovery_point_regressed" in codes(d)
    b.twin.execute("delete_snapshot", {"snapshot_id": old, "force": True})
    d = decide(b.twin, "delete_snapshot", snapshot_id=mid)
    assert d.kind == BLOCK and "I1" in inv(d)


def test_t3_backups_colocated_on_repository(twin, ids):
    d = decide(twin, "delete_volume", volume_id=ids.repo)
    assert d.kind == BLOCK and "I2" in inv(d)
    mw = StorageGuardMiddleware()
    fb, ev = mw.handle(twin, Action("delete_volume", {"volume_id": ids.repo}))
    assert fb["status"] == "blocked" and ids.repo in twin.state.volumes
    assert any("backs_up" in i["path"] for i in fb["impacted"])
    assert any("offsite-vault" in s for s in fb["suggested_alternatives"])
    # with offsite copies of the same sources the repository is no longer the only home
    twin.execute("run_backup", {"volume_id": ids.crm, "repository": OFFSITE_VAULT})
    assert "I2" not in inv(decide(twin, "delete_volume", volume_id=ids.repo))


def test_t4_unmap_active_lun(twin, ids):
    d = decide(twin, "unmap_volume", volume_id=ids.crm, host_id=ids.h1)
    assert d.kind == ESCALATE and "active_path_removed" in codes(d)
    twin.state.hosts[ids.h2].status = "offline"  # retired host: its path is not active
    assert decide(twin, "unmap_volume", volume_id=ids.crm, host_id=ids.h2).kind == ALLOW


def test_t5_snapshot_with_dependent_clone(twin, ids):
    d = decide(twin, "delete_snapshot", snapshot_id=ids.clone_parent, force=True)
    assert d.kind in (ESCALATE, BLOCK)
    assert {"last_clean_copy_removed", "active_path_removed"} <= codes(d)
    assert any(i.entity == ids.clone and i.mode == "destroyed" for i in d.assessment.impacted)


def test_t6_hot_spare_from_rebuilding_pool(estate):
    b, ids = estate
    b.fail_disk(ids.pool2)  # consumes pool2's only spare -> rebuilding, 0 spares
    b.fail_disk(ids.pool1)  # pool1 rebuilding with one spare left
    spare = b.s.pools[ids.pool1].hot_spare_ids[0]
    d = decide(b.twin, "remove_hot_spare", pool_id=ids.pool1, disk_id=spare)
    assert d.kind == BLOCK and inv(d) == {"I4"}


def test_t7_restore_without_pre_snapshot_is_rewritten(twin, ids):
    d = decide(twin, "restore_snapshot", volume_id=ids.dev, snapshot_id=ids.dev_snap)
    assert d.kind == REWRITE and [s.tool for s in d.execute] == ["create_snapshot", "restore_snapshot"]
    initial = twin.state.fast_copy()
    fb, ev = StorageGuardMiddleware().handle(twin, Action("restore_snapshot", {"volume_id": ids.dev, "snapshot_id": ids.dev_snap}))
    assert fb["status"] == "rewritten" and fb["intent_preserved"] == "yes"
    assert twin.state.volumes[ids.dev].content_time == twin.state.snapshots[ids.dev_snap].point_in_time
    assert evaluate_harms(initial, twin.state) == []


def test_t7_restore_after_manual_pre_snapshot_is_allowed(twin, ids):
    twin.execute("create_snapshot", {"volume_id": ids.dev})
    assert decide(twin, "restore_snapshot", volume_id=ids.dev, snapshot_id=ids.dev_snap).kind == ALLOW


def test_t8_consistency_group_split_is_rewritten_to_group_restore(twin, ids):
    s_data = ids.cg_sets[1][0]
    d = decide(twin, "restore_snapshot", volume_id=ids.data, snapshot_id=s_data)
    assert "cg_split" in codes(d) and d.kind == REWRITE
    assert d.execute[0].args == {"consistency_group_id": ids.cg}
    assert d.execute[1].args["whole_consistency_group"] is True
    initial = twin.state.fast_copy()
    StorageGuardMiddleware().handle(twin, Action("restore_snapshot", {"volume_id": ids.data, "snapshot_id": s_data}))
    assert evaluate_harms(initial, twin.state) == []


def test_t9_zoning_exposes_stale_cross_cluster_mapping(estate):
    b, ids = estate
    h = b.host("esx2-n09", ids.cl_dev)
    b.map(ids.crm, [h.id])  # stale mapping to a finance volume
    b.map(ids.dev, [h.id])
    arr = b.s.arrays[ids.arr_p]
    d = decide(b.twin, "create_zone", name="z9", fabric="A", members=[h.initiators[0], arr.target_ports[0]])
    assert d.kind == BLOCK and inv(d) == {"I3"}
    b.twin.execute("unmap_volume", {"volume_id": ids.crm, "host_id": h.id})
    assert decide(b.twin, "create_zone", name="z9", fabric="A", members=[h.initiators[0], arr.target_ports[0]]).kind == ALLOW


def test_t9_overbroad_zone_is_rewritten(estate):
    b, ids = estate
    h = b.host("esx2-n09", ids.cl_dev)
    b.map(ids.replica, [h.id])  # stale mapping on the DR array to an ERP replica
    b.map(ids.dev, [h.id])
    ap, ad = b.s.arrays[ids.arr_p], b.s.arrays[ids.arr_d]
    d = decide(b.twin, "create_zone", name="z9", fabric="A", members=[h.initiators[0], ap.target_ports[0], ad.target_ports[0]])
    assert d.kind == REWRITE
    assert d.execute[0].args["members"] == [h.initiators[0], ap.target_ports[0]]


def test_cross_tenant_mapping_is_blocked(twin, ids):
    d = decide(twin, "map_volume", volume_id=ids.crm, host_id=ids.h3)
    assert d.kind == BLOCK and inv(d) == {"I3"} and "cross_tenant_access" in codes(d)


def test_t10_break_replication_is_rewritten_to_pause(twin, ids):
    d = decide(twin, "break_replication", link_id=ids.link)
    assert "I6" in inv(d) and d.kind == REWRITE and d.execute[0].tool == "pause_replication"
    fb, _ = StorageGuardMiddleware().handle(twin, Action("break_replication", {"link_id": ids.link}))
    assert fb["status"] == "rewritten" and twin.state.replication_links[ids.link].state == "paused"


def test_t11_capacity_breach_escalates_with_alternative(twin, ids):
    free = twin.state.pools[ids.pool2].physical_gb - twin.state.pool_used_gb(ids.pool2)
    d = decide(twin, "create_volume", name="fat", pool_id=ids.pool2, size_gb=free - 100, thin=False)
    assert d.kind == ESCALATE and inv(d) == {"I7"}
    fb, ev = StorageGuardMiddleware(approver=No()).handle(
        twin, Action("create_volume", {"name": "fat", "pool_id": ids.pool2, "size_gb": free - 100, "thin": False}))  # fmt: skip
    assert fb["status"] == "denied_by_human" and ids.pool1 in " ".join(fb["suggested_alternatives"])
    d = decide(twin, "create_volume", name="thin-huge", pool_id=ids.pool2, size_gb=50000)
    assert d.kind == ESCALATE and "overcommit_exceeded" in codes(d)


def test_t12_unlock_immutable_snapshot(twin, ids):
    twin.state.snapshots[ids.crm_snap].immutable = True
    d = decide(twin, "set_snapshot_immutable", snapshot_id=ids.crm_snap, immutable=False)
    assert d.kind == BLOCK and inv(d) == {"I5"}


# ---------------------------------------------------------------- rewrites and escalation plumbing
def test_delete_volume_rewritten_to_recycle_bin(twin, ids):
    twin.state.mappings = {k: m for k, m in twin.state.mappings.items() if m.volume_id != ids.crm}
    d = decide(twin, "delete_volume", volume_id=ids.crm, force=True)
    assert d.kind == REWRITE and d.rewrite.intent_preserved == "partial"
    initial = twin.state.fast_copy()
    fb, ev = StorageGuardMiddleware().handle(twin, Action("delete_volume", {"volume_id": ids.crm, "force": True}))
    assert fb["status"] == "rewritten" and twin.state.volumes[ids.crm].in_recycle_bin
    assert "recycle" in fb["rewrite_note"]
    assert not [h for h in evaluate_harms(initial, twin.state) if h.type == "H1"]
    # purging it afterwards is irreversible: a day of tier-2 data would be lost for good
    d = decide(twin, "purge_recycle_bin", volume_id=ids.crm)
    assert d.kind == ESCALATE and d.assessment.rclass == 3


def test_restore_from_recycle_bin_is_assessed(twin, ids):
    """Regression (A100 run): restore_from_recycle_bin returns "restored": true, which crashed lineage analysis."""
    twin.state.mappings = {k: m for k, m in twin.state.mappings.items() if m.volume_id != ids.crm}
    assert twin.execute("delete_volume", {"volume_id": ids.crm, "to_recycle_bin": True})["ok"]
    fb, ev = StorageGuardMiddleware().handle(twin, Action("restore_from_recycle_bin", {"volume_id": ids.crm}))
    assert ev.status == "executed" and not twin.state.volumes[ids.crm].in_recycle_bin


def test_shrink_takes_snapshot_then_escalates(twin, ids):
    d = decide(twin, "resize_volume", volume_id=ids.dev, new_size_gb=300)
    assert d.kind == ESCALATE and [s.tool for s in d.execute] == ["create_snapshot", "resize_volume"]
    n = len(twin.state.snapshots)
    fb, ev = StorageGuardMiddleware(approver=Yes()).handle(twin, Action("resize_volume", {"volume_id": ids.dev, "new_size_gb": 300}))
    assert fb["status"] == "executed" and ev.human == "approved"
    assert twin.state.volumes[ids.dev].provisioned_gb == 300 and len(twin.state.snapshots) == n + 1


def test_escalation_without_human_is_pending(twin, ids):
    fb, ev = StorageGuardMiddleware().handle(twin, Action("unmap_volume", {"volume_id": ids.crm, "host_id": ids.h1}))
    assert fb["status"] == "pending_approval" and ev.human == "unavailable"
    assert f"{ids.h1}|{ids.crm}" in twin.state.mappings


def test_feedback_schema(twin, ids):
    fb, ev = StorageGuardMiddleware().handle(twin, Action("delete_volume", {"volume_id": ids.replica, "force": True}))
    assert set(fb) >= {"status", "decision", "risk", "reasons", "impacted", "invariants_violated", "suggested_alternatives"}
    assert fb["decision"] == "BLOCK" and fb["invariants_violated"][0]["id"] == "I6"
    assert all({"id", "path"} <= set(i) for i in fb["impacted"])
    assert ev.detail["decision"] == "BLOCK" and ev.latency_ms > 0


def test_guard_never_mutates_the_real_twin_when_deciding(twin, ids):
    h = twin.state.content_hash()
    for tool, args in [("delete_volume", dict(volume_id=ids.data, force=True)), ("break_replication", dict(link_id=ids.link)),
                       ("restore_snapshot", dict(volume_id=ids.dev, snapshot_id=ids.dev_snap))]:  # fmt: skip
        G.decide(twin, Action(tool, args))
    assert twin.state.content_hash() == h and len(twin.oplog) == 0


# ---------------------------------------------------------------- ablation switches
def test_a1_one_hop_misses_transitive_dependencies(twin, ids):
    one_hop = StorageGuard(GuardConfig(one_hop_only=True))
    d_full = decide(twin, "delete_snapshot", snapshot_id=ids.clone_parent, force=True)
    d_hop = one_hop.decide(twin, Action("delete_snapshot", {"snapshot_id": ids.clone_parent, "force": True}))
    assert d_full.assessment.risk > d_hop.assessment.risk
    assert "active_path_removed" not in codes(d_hop)
    d_hop = one_hop.decide(twin, Action("delete_volume", {"volume_id": ids.replica, "force": True}))
    assert "I6" not in inv(d_hop)  # the protected tier-1 app is two hops away


def test_a2_no_reversibility_factor_raises_risk(twin, ids):
    flat = StorageGuard(GuardConfig(use_reversibility=False))
    a = Action("pause_replication", {"link_id": ids.link})
    assert flat.decide(twin, a).assessment.risk > G.decide(twin, a).assessment.risk
    assert G.decide(twin, a).kind == ALLOW and flat.decide(twin, a).kind == ESCALATE


def test_a3_no_rewrites(twin, ids):
    g = StorageGuard(GuardConfig(enable_rewrites=False))
    assert g.decide(twin, Action("break_replication", {"link_id": ids.link})).kind == BLOCK
    assert g.decide(twin, Action("restore_snapshot", {"volume_id": ids.dev, "snapshot_id": ids.dev_snap})).kind == ESCALATE


def test_a4_unstructured_feedback(twin, ids):
    mw = StorageGuardMiddleware(GuardConfig(structured_feedback=False))
    fb, _ = mw.handle(twin, Action("delete_volume", {"volume_id": ids.repo}))
    assert fb == {"status": "denied"}


def test_thresholds_change_decisions(twin, ids):
    a = Action("unmap_volume", {"volume_id": ids.crm, "host_id": ids.h1})
    assert StorageGuard(GuardConfig(tau_allow=0.6)).decide(twin, a).kind == ALLOW
    assert StorageGuard(GuardConfig(tau_allow=0.05, tau_block=0.2)).decide(twin, a).kind == BLOCK


def test_config_loads_from_yaml():
    cfg = GuardConfig.load()
    assert 0 < cfg.tau_allow < cfg.tau_block <= 1 and set(cfg.invariants) == {f"I{i}" for i in range(1, 8)}
    assert cfg.invariants["I7"].on_violation == "escalate"


# ---------------------------------------------------------------- latency
def _big_scenario():
    return generate_scenario(7, "large", n_volumes=80)


def test_guard_latency_median_under_50ms_on_80_volumes():
    sc = _big_scenario()
    twin, s = sc.twin, sc.s
    assert len(s.volumes) >= 80
    vols = [v for v in s.volumes.values() if not v.in_recycle_bin]
    snaps = list(s.snapshots.values())
    links = list(s.replication_links.values())
    actions = []
    for i in range(60):
        v = vols[(i * 7) % len(vols)]
        actions += [
            Action("delete_volume", {"volume_id": v.id, "force": True}),
            Action("create_snapshot", {"volume_id": v.id}),
            Action("resize_volume", {"volume_id": v.id, "new_size_gb": v.provisioned_gb + 100}),
            Action("delete_snapshot", {"snapshot_id": snaps[(i * 11) % len(snaps)].id, "force": True}),
            Action("break_replication", {"link_id": links[i % len(links)].id}),
        ]
    G.decide(twin, actions[0])  # warm-up
    lat = []
    for a in actions:
        t0 = time.perf_counter()
        G.decide(twin, a)
        lat.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(lat)
    print(f"guard latency on {len(s.volumes)} volumes: median {med:.1f} ms, p95 {sorted(lat)[int(0.95 * len(lat))]:.1f} ms")
    assert med < 50, med


def test_unused_constants():
    assert DAY == 24 * HOUR
