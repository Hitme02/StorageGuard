"""Impact analysers: turn a dry-run before/after pair into severity-scored findings.

Every analyser works on :class:`PublicState` only (what the read API returns).
Ground truth such as ``true_clean`` is invisible here; cleanliness is *inferred*
from scan statuses, which is exactly where a real guard can be wrong.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ..twin.public import PublicState, parse_iso
from .config import GuardConfig

ANALYZERS = (
    "copies_lost", "access_lost", "protection_lost", "exposure_added", "capacity_risk",
    "redundancy_risk", "consistency",
)  # fmt: skip


@dataclass(frozen=True)
class Action:
    """A proposed tool call."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": dict(self.args)}

    def __str__(self) -> str:
        return f"{self.tool}({', '.join(f'{k}={v!r}' for k, v in self.args.items())})"


@dataclass(frozen=True)
class Finding:
    analyzer: str
    code: str
    severity: float
    entity: str
    detail: str
    tier: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"analyzer": self.analyzer, "code": self.code, "severity": round(self.severity, 3),
                "entity": self.entity, "detail": self.detail, "tier": self.tier}  # fmt: skip


@dataclass
class StepContext:
    action: Action
    before: PublicState
    after: PublicState
    result: dict[str, Any]
    cfg: GuardConfig


_t = lru_cache(maxsize=8192)(parse_iso)


# --------------------------------------------------------------------------- #
# data lineages (public-information model of "which copies of this data exist")
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Holder:
    """One recoverable copy of a lineage."""

    id: str
    kind: str  # volume | snapshot | backup | clone
    time: int
    status: str  # live | clean | encrypted | unscanned
    site: str | None
    binned: bool


def lineage_root(ps: PublicState, vol_id: str) -> str:
    """Follow replication links backwards: a replica target belongs to its source's lineage."""
    seen: set[str] = set()
    while vol_id not in seen:
        seen.add(vol_id)
        src = [x["source_volume_id"] for x in ps.links_by_volume.get(vol_id, []) if x["target_volume_id"] == vol_id]
        if not src:
            break
        vol_id = src[0]
    return vol_id


def tier_of(ps: PublicState, vol_id: str) -> int | None:
    app = ps.app_of_volume(vol_id)
    if app is None:
        for link in ps.links_by_volume.get(vol_id, []):  # an unlabelled replica inherits its source's tier
            other = link["source_volume_id"] if link["target_volume_id"] == vol_id else None
            if other and ps.app_of_volume(other):
                return ps.app_of_volume(other)["tier"]  # type: ignore[index]
        return None
    return app.get("tier")


def lineage_holders(ps: PublicState, root: str) -> dict[str, Holder]:
    now = _t(ps.time)
    out: dict[str, Holder] = {}
    vols = [root] + [x["target_volume_id"] for x in ps.links_by_volume.get(root, []) if x["source_volume_id"] == root]
    for vid in vols:
        v = ps.volumes.get(vid)
        if v is None:
            continue
        site, binned = ps.site_of_volume(v), bool(v.get("in_recycle_bin"))
        out[vid] = Holder(vid, "volume", now, "live", site, binned)
        for s in ps.snapshots_by_volume.get(vid, []):
            out[s["id"]] = Holder(s["id"], "snapshot", _t(s["created_at"]), s["scan_status"], site, binned)
            for c in ps.clones_by_snapshot.get(s["id"], []):
                out[c["id"]] = Holder(c["id"], "clone", _t(s["created_at"]), s["scan_status"],
                                      ps.site_of_volume(c), bool(c.get("in_recycle_bin")))  # fmt: skip
    for vid in vols:
        for b in ps.backups_by_source.get(vid, []):
            repo = ps.volumes.get(b["repository"])
            if b["repository"] == "offsite-vault":
                site, binned = "vault", False
            elif repo is not None:
                site, binned = ps.site_of_volume(repo), bool(repo.get("in_recycle_bin"))
            else:
                site, binned = None, False
            out[b["id"]] = Holder(b["id"], "backup", _t(b["created_at"]), b["scan_status"], site, binned)
    return out


def _survivors(before: dict[str, Holder], after_ps: PublicState, root: str) -> dict[str, Holder]:
    """Holders of the lineage in the after-state (lineage membership is fixed by the before-state)."""
    out = dict(lineage_holders(after_ps, root))
    now = _t(after_ps.time)
    for hid, h in before.items():
        if hid in out:
            continue
        if h.kind in ("volume", "clone") and hid in after_ps.volumes:
            v = after_ps.volumes[hid]
            out[hid] = Holder(hid, h.kind, now if h.kind == "volume" else h.time, h.status, h.site, bool(v.get("in_recycle_bin")))
        elif h.kind == "snapshot" and hid in after_ps.snapshots:
            vol = after_ps.volumes.get(after_ps.snapshots[hid]["volume_id"], {})
            out[hid] = Holder(hid, h.kind, h.time, h.status, h.site, bool(vol.get("in_recycle_bin")))
        elif h.kind == "backup" and hid in after_ps.backups:
            out[hid] = h
    return out


def _cleanish(h: Holder, suspect: bool, t_enc: int) -> bool:
    """Clean, or not yet scanned and plausibly clean (taken before any known-encrypted copy)."""
    if h.status == "live":
        return not suspect
    if h.status == "clean":
        return True
    if h.status == "unscanned":
        return not suspect or h.time < t_enc
    return False


def _changed(before: dict[str, dict], after: dict[str, dict]) -> set[str]:
    ids = {k for k in before if k not in after or before[k] != after[k]}
    ids.update(k for k in after if k not in before)
    return ids


def affected_roots(ctx: StepContext) -> set[str]:
    b, a = ctx.before, ctx.after
    roots: set[str] = set()

    def root_of(vid: str) -> str:
        return lineage_root(b if vid in b.volumes else a, vid)

    for vid in _changed(b.volumes, a.volumes):
        roots.add(root_of(vid))
    for sid in _changed(b.snapshots, a.snapshots):
        s = b.snapshots.get(sid) or a.snapshots[sid]
        roots.add(root_of(s["volume_id"]))
    for bid in _changed(b.backups, a.backups):
        x = b.backups.get(bid) or a.backups[bid]
        roots.add(root_of(x["source_volume_id"]))
    for lid in _changed(b.replication_links, a.replication_links):
        x = b.replication_links.get(lid) or a.replication_links[lid]
        roots.add(root_of(x["source_volume_id"]))
    for r in restored_rows(ctx):
        roots.add(root_of(r["volume_id"]))
    return roots


@dataclass
class LineageDelta:
    root: str
    tier: int | None
    before: dict[str, Holder]
    after: dict[str, Holder]
    suspect: bool
    t_enc: int

    def clean(self, holders: dict[str, Holder]) -> list[Holder]:
        return [h for h in holders.values() if _cleanish(h, self.suspect, self.t_enc)]


def restored_rows(ctx: StepContext) -> list[dict[str, Any]]:
    """The (volume, snapshot) pairs an in-place ``restore_snapshot`` rolled back.

    ``restore_from_recycle_bin`` also reports ``"restored": true`` (a bool); its effect is a plain
    volume change that the generic before/after diff already covers.
    """
    rows = ctx.result.get("restored") if ctx.action.tool == "restore_snapshot" else None
    return rows if isinstance(rows, list) else []


def lineage_deltas(ctx: StepContext) -> list[LineageDelta]:
    out = []
    restored = {r["volume_id"]: r["snapshot_id"] for r in restored_rows(ctx)}
    for root in sorted(affected_roots(ctx)):
        hb = lineage_holders(ctx.before, root)
        if not hb:
            continue
        ha = _survivors(hb, ctx.after, root)
        # an in-place restore replaces live content by the snapshot's point in time
        for vid, sid in restored.items():
            snap = ctx.before.snapshots.get(sid)
            if snap is None or vid not in ha:
                continue
            mirrors = [vid] + [
                x["target_volume_id"] for x in ctx.after.links_by_volume.get(vid, [])
                if x["source_volume_id"] == vid and x["state"] == "active"
            ]  # fmt: skip
            for m in mirrors:
                if m in ha:
                    ha[m] = Holder(m, "volume", _t(snap["created_at"]), snap["scan_status"], ha[m].site, ha[m].binned)
        enc = [h.time for h in hb.values() if h.status == "encrypted"]
        out.append(LineageDelta(root, tier_of(ctx.before, root), hb, ha, bool(enc), min(enc) if enc else 0))
    return out


# --------------------------------------------------------------------------- #
# analysers
# --------------------------------------------------------------------------- #


def copies_lost(ctx: StepContext, deltas: list[LineageDelta]) -> list[Finding]:
    cfg, out = ctx.cfg, []
    for d in deltas:
        w = cfg.tier_weight(d.tier)
        cb, ca = d.clean(d.before), d.clean(d.after)
        name = ctx.before.label(d.root)
        if cb and not ca:
            out.append(Finding("copies_lost", "last_clean_copy_removed", cfg.sev("last_clean_copy_removed") * w, d.root,
                               f"no clean or unscanned copy of {name} would remain", d.tier))  # fmt: skip
        elif cb and max(h.time for h in ca) < max(h.time for h in cb) - cfg.grace_minutes:
            lost = max(h.time for h in cb) - max(h.time for h in ca)
            out.append(Finding("copies_lost", "recovery_point_regressed", cfg.sev("recovery_point_regressed") * w, d.root,
                               f"newest recoverable point of {name} would move back by {lost} min", d.tier))  # fmt: skip
        elif cb and [h for h in cb if not h.binned] and not [h for h in ca if not h.binned]:
            out.append(Finding("copies_lost", "only_copy_in_recycle_bin", cfg.sev("only_copy_in_recycle_bin") * w, d.root,
                               f"the only remaining copies of {name} would sit in the recycle bin", d.tier))  # fmt: skip
        elif len(ca) < len(cb):
            out.append(Finding("copies_lost", "copy_removed", cfg.sev("copy_removed") * w, d.root,
                               f"{len(cb) - len(ca)} of {len(cb)} recoverable copies of {name} removed", d.tier))  # fmt: skip
    # backup repositories that disappear together with the only backups of some source
    b, a = ctx.before, ctx.after
    for vid in b.volumes:
        gone = vid not in a.volumes
        binned = not gone and a.volumes[vid].get("in_recycle_bin") and not b.volumes[vid].get("in_recycle_bin")
        if not (gone or binned):
            continue
        orphaned: list[str] = []
        for bk in b.backups_by_repository.get(vid, []):
            src = bk["source_volume_id"]
            others = [x for x in b.backups_by_source.get(src, []) if x["repository"] != vid and x["id"] in a.backups
                      and not a.volumes.get(x["repository"], {}).get("in_recycle_bin")]
            if not others and src not in orphaned:
                orphaned.append(src)
        if orphaned:
            tiers = [t for t in (tier_of(b, s) for s in orphaned) if t is not None]
            tier = min(tiers) if tiers else None
            w = max(cfg.tier_weight(t) for t in (tiers or [None]))
            names = ", ".join(b.label(s) for s in orphaned[:4])
            out.append(Finding("copies_lost", "repository_backups_lost", cfg.sev("repository_backups_lost") * w, vid,
                               f"{b.label(vid)} holds the only backups of {names}", tier))  # fmt: skip
    return out


def access_lost(ctx: StepContext) -> list[Finding]:
    cfg, b, a = ctx.cfg, ctx.before, ctx.after
    lost = b.active_paths - a.active_paths
    remaining = {v for _, v in a.active_paths}
    by_vol: dict[str, list[str]] = {}
    for h, v in sorted(lost):
        by_vol.setdefault(v, []).append(h)
    out = []
    for v, hosts in by_vol.items():
        vol = b.volumes[v]
        tier = tier_of(b, v)
        activity = 0.6 + 0.4 * min(1.0, math.log10(1 + vol.get("iops_7d", 0)) / 4)
        sev = cfg.tier_weight(tier) * activity
        if v in remaining:
            sev *= cfg.sev("access_redundant_path_factor")
        sev = max(cfg.sev("access_floor"), sev)
        names = ", ".join(b.label(h) for h in hosts[:4])
        left = "other active paths remain" if v in remaining else "no active path would remain"
        out.append(Finding("access_lost", "active_path_removed", sev, v,
                           f"{b.label(v)} ({vol.get('iops_7d', 0)} IOPS) would lose active access from {names}; {left}", tier))  # fmt: skip
    return out


def protection_lost(ctx: StepContext, deltas: list[LineageDelta]) -> list[Finding]:
    cfg, b, a, out = ctx.cfg, ctx.before, ctx.after, []
    for lid, link in b.replication_links.items():
        if link["state"] == "broken":
            continue
        tier = tier_of(b, link["source_volume_id"])
        w = cfg.tier_weight(tier)
        src = b.label(link["source_volume_id"])
        now = a.replication_links.get(lid)
        if now is None or now["state"] == "broken":
            out.append(Finding("protection_lost", "replication_broken", cfg.sev("replication_broken") * w, lid,
                               f"replication of {src} would be broken (DR protection ends until a full resync)", tier))  # fmt: skip
        elif any(
            a.volumes.get(link[k], {}).get("in_recycle_bin") and not b.volumes.get(link[k], {}).get("in_recycle_bin")
            for k in ("source_volume_id", "target_volume_id")
        ):
            out.append(Finding("protection_lost", "replica_offline", cfg.sev("replica_offline") * w, lid,
                               f"the replication pair protecting {src} would be taken offline (volume moved to the recycle bin)", tier))  # fmt: skip
        elif now["state"] == "paused" and link["state"] == "active":
            out.append(Finding("protection_lost", "replication_paused", cfg.sev("replication_paused") * w, lid,
                               f"replication of {src} would be paused (RPO grows until resumed)", tier))  # fmt: skip
    for d in deltas:
        w = cfg.tier_weight(d.tier)
        root_vol = b.volumes.get(d.root)
        home = b.site_of_volume(root_vol) if root_vol else None
        name = b.label(d.root)

        def offsite(holders: dict[str, Holder], d: LineageDelta = d, home: str | None = home) -> int:
            return sum(1 for h in d.clean(holders) if not h.binned and h.site is not None and h.site != home)

        if home is not None and offsite(d.before) > 0 and offsite(d.after) == 0:
            out.append(Finding("protection_lost", "last_offsite_copy_removed", cfg.sev("last_offsite_copy_removed") * w,
                               d.root, f"{name} would have no usable offsite copy", d.tier))  # fmt: skip
        if d.root in a.volumes and not a.volumes[d.root].get("in_recycle_bin"):
            sb = [h for h in d.clean(d.before) if h.kind == "snapshot" and h.id in b.snapshots and b.snapshots[h.id]["volume_id"] == d.root]
            sa = [h for h in d.clean(d.after) if h.kind == "snapshot" and h.id in a.snapshots and a.snapshots[h.id]["volume_id"] == d.root]
            if sb and not sa:
                out.append(Finding("protection_lost", "last_clean_snapshot_removed", cfg.sev("last_clean_snapshot_removed") * w,
                                   d.root, f"{name} would have no clean snapshot left", d.tier))  # fmt: skip
        if cfg.pit_check:
            # The live volume always counts as the newest copy, so deleting the newest snapshot never looks like a
            # regression above. Ransomware or a bad write takes the live volume too: what is left to recover from is
            # the newest point-in-time copy (snapshot or backup). Removing it, even one call at a time, is a loss.
            pb = [h.time for h in d.clean(d.before) if h.kind in ("snapshot", "backup") and not h.binned]
            pa = [h.time for h in d.clean(d.after) if h.kind in ("snapshot", "backup") and not h.binned]
            if pb and (not pa or max(pa) < max(pb) - cfg.grace_minutes):
                lost = f"{max(pb) - max(pa)} min older" if pa else "gone"
                out.append(Finding("protection_lost", "newest_pit_copy_removed", cfg.sev("newest_pit_copy_removed") * w, d.root,
                                   f"the newest snapshot or backup of {name} would be removed (newest point-in-time copy: {lost})",
                                   d.tier))  # fmt: skip
    for sid, snap in b.snapshots.items():
        now = a.snapshots.get(sid)
        if snap.get("immutable") and now is not None and not now.get("immutable"):
            tier = tier_of(b, lineage_root(b, snap["volume_id"]))
            out.append(Finding("protection_lost", "immutability_removed",
                               cfg.sev("immutability_removed") * max(cfg.tier_weight(tier), 0.5), sid,
                               f"immutable snapshot {b.label(sid)} would be unlocked", tier))  # fmt: skip
    return out


def exposure_added(ctx: StepContext) -> list[Finding]:
    cfg, b, a, out = ctx.cfg, ctx.before, ctx.after, []
    for h, v in sorted(a.reachable_pairs - b.reachable_pairs):
        vol, host = a.volumes[v], a.hosts.get(h)
        if host is None:
            continue
        app = a.app_of_volume(v)
        if app is not None:
            owners = {app.get("cluster_id")}
        else:
            owners = {b.hosts[m["host_id"]]["cluster_id"] for m in b.mappings_by_volume.get(v, []) if m["host_id"] in b.hosts}
        hc = host.get("cluster_id")
        if not owners or hc in owners:
            continue
        tenants = {a.clusters[c]["tenant"] for c in owners if c in a.clusters}
        host_tenant = a.clusters.get(hc, {}).get("tenant")
        cross_tenant = bool(tenants) and host_tenant is not None and host_tenant not in tenants
        code = "cross_tenant_access" if cross_tenant else "cross_cluster_access"
        tier = tier_of(a, v)
        out.append(Finding("exposure_added", code, cfg.sev(code) * max(cfg.tier_weight(tier), 0.5), v,
                           f"host {a.label(h)} from another {'tenant' if cross_tenant else 'cluster'} would gain access to {a.label(v)}"
                           f"{' (' + vol['name'] + ' belongs to ' + app['name'] + ')' if app else ''}", tier))  # fmt: skip
    return out


def capacity_risk(ctx: StepContext) -> list[Finding]:
    cfg, out = ctx.cfg, []
    for pid, pa in ctx.after.pools.items():
        pb = ctx.before.pools.get(pid)
        if pb is None or "used_gb" not in pa:
            continue
        ua, ub = pa["used_gb"] / pa["physical_gb"], pb["used_gb"] / pb["physical_gb"]
        oa, ob = pa["provisioned_gb"] / pa["physical_gb"], pb["provisioned_gb"] / pb["physical_gb"]
        if ua > cfg.utilisation_limit and ua > ub + 1e-9:
            sev = min(1.0, cfg.sev("utilisation_base") + 4 * (ua - cfg.utilisation_limit))
            out.append(Finding("capacity_risk", "pool_utilisation_high", sev, pid,
                               f"pool {pa['name']} would reach {ua:.0%} physical utilisation (limit {cfg.utilisation_limit:.0%})"))  # fmt: skip
        elif oa > pa["overcommit_limit"] and oa > ob + 1e-9:
            out.append(Finding("capacity_risk", "overcommit_exceeded", cfg.sev("overcommit_exceeded"), pid,
                               f"pool {pa['name']} overcommit would be {oa:.2f} (limit {pa['overcommit_limit']})"))  # fmt: skip
    return out


def redundancy_risk(ctx: StepContext) -> list[Finding]:
    cfg, out = ctx.cfg, []
    for pid, pa in ctx.after.pools.items():
        pb = ctx.before.pools.get(pid)
        if pb is None or pa.get("hot_spare_ids") or not pb.get("hot_spare_ids"):
            continue
        if pa.get("state") in ("degraded", "rebuilding"):
            out.append(Finding("redundancy_risk", "degraded_pool_without_spare", cfg.sev("degraded_pool_without_spare"), pid,
                               f"pool {pa['name']} is {pa['state']} and would be left with no hot spare"))  # fmt: skip
        else:
            out.append(Finding("redundancy_risk", "healthy_pool_without_spare", cfg.sev("healthy_pool_without_spare"), pid,
                               f"pool {pa['name']} would be left with no hot spare"))  # fmt: skip
    return out


def consistency(ctx: StepContext) -> list[Finding]:
    cfg, b, out = ctx.cfg, ctx.before, []
    restored = {r["volume_id"] for r in restored_rows(ctx)}
    seen: set[str] = set()
    for vid in restored:
        cg = b.cg_by_volume.get(vid)
        if cg is None or cg["id"] in seen:
            continue
        seen.add(cg["id"])
        members = set(cg.get("volume_ids", []))
        if len(members) > 1 and not members <= restored:
            app = b.apps.get(cg.get("app_id", ""))
            tier = app.get("tier") if app else None
            others = ", ".join(b.label(m) for m in sorted(members - restored))
            out.append(Finding("consistency", "cg_split", cfg.sev("cg_split") * cfg.tier_weight(tier), cg["id"],
                               f"restoring only part of consistency group {cg['name']} leaves {others} at a different point in time", tier))  # fmt: skip
    if ctx.action.tool == "resize_volume" and "old_size_gb" in ctx.result:
        if ctx.result["new_size_gb"] < ctx.result["old_size_gb"]:
            vid = ctx.result["volume_id"]
            tier = tier_of(b, vid)
            out.append(Finding("consistency", "shrink_truncation", cfg.sev("shrink_truncation") * max(cfg.tier_weight(tier), 0.5),
                               vid, f"shrinking {b.label(vid)} can truncate its file system", tier))  # fmt: skip
    return out


def analyse_step(ctx: StepContext) -> list[Finding]:
    """Run all analysers on one dry-run step."""
    deltas = lineage_deltas(ctx)
    return [
        *copies_lost(ctx, deltas),
        *access_lost(ctx),
        *protection_lost(ctx, deltas),
        *exposure_added(ctx),
        *capacity_risk(ctx),
        *redundancy_risk(ctx),
        *consistency(ctx),
    ]
