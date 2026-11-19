"""Every tool the agent can call, implemented against :class:`StorageTwin`.

Each operation is a plain function ``fn(twin, **kwargs) -> dict`` registered
with :func:`op`.  Tool JSON schemas are generated from the signatures and
docstrings (see :mod:`storageguard.agent.tools`), so the docstrings double as
the tool descriptions: the first line says what the tool does, the ``Concept``
line teaches the storage idea behind it, and ``Args`` documents parameters.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict, ValidationError, create_model

from .models import (
    OFFSITE_VAULT,
    BackupCopy,
    Mapping,
    ReplicationLink,
    Snapshot,
    Volume,
    Zone,
    mapping_key,
)
from .public import iso, to_public
from .state import RECYCLE_TTL_MINUTES, OpError, StorageTwin


@dataclass(frozen=True)
class OpSpec:
    name: str
    fn: Callable[..., dict]
    mutating: bool
    destructive: bool
    group: str
    arg_model: Any


OPS: dict[str, OpSpec] = {}

#: argument name -> collection it refers to (used for name->id resolution and by the guard)
ARG_COLLECTION: dict[str, str] = {
    "volume_id": "volumes",
    "source_volume_id": "volumes",
    "host_id": "hosts",
    "pool_id": "pools",
    "target_pool_id": "pools",
    "snapshot_id": "snapshots",
    "zone_id": "zones",
    "link_id": "replication_links",
    "backup_id": "backups",
    "app_id": "apps",
    "consistency_group_id": "consistency_groups",
    "disk_id": "disks",
    "cluster_id": "clusters",
}


def op(*, mutating: bool = False, destructive: bool = False, group: str = "read") -> Callable:
    """Register a twin operation as a tool."""

    def deco(fn: Callable[..., dict]) -> Callable[..., dict]:
        sig = inspect.signature(fn)
        fields: dict[str, Any] = {}
        for p in list(sig.parameters.values())[1:]:
            fields[p.name] = (p.annotation, ... if p.default is inspect.Parameter.empty else p.default)
        model = create_model(f"{fn.__name__}_args", __config__=ConfigDict(extra="forbid"), **fields)
        OPS[fn.__name__] = OpSpec(fn.__name__, fn, mutating, destructive, group, model)
        return fn

    return deco


def validate_args(spec: OpSpec, args: dict[str, Any]) -> dict[str, Any]:
    """Validate/coerce LLM-supplied arguments; raises ``ValueError`` with a short message."""
    try:
        parsed = spec.arg_model.model_validate(args)
    except ValidationError as exc:
        msgs = [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise ValueError("; ".join(msgs)) from None
    return {k: getattr(parsed, k) for k in spec.arg_model.model_fields}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _get(twin: StorageTwin, kind: str, key: str | None, label: str) -> Any:
    ent = getattr(twin.state, kind).get(key) if key else None
    if ent is None:
        raise OpError(f"{label} '{key}' not found")
    return ent


def _vol(twin: StorageTwin, volume_id: str, *, allow_binned: bool = False) -> Volume:
    v = _get(twin, "volumes", volume_id, "volume")
    if v.in_recycle_bin and not allow_binned:
        raise OpError(f"volume '{volume_id}' is in the recycle bin")
    return v


def _pool_row(twin: StorageTwin, pool_id: str) -> dict:
    s = twin.state
    p = s.pools[pool_id]
    used, prov = s.pool_used_gb(pool_id), s.pool_provisioned_gb(pool_id)
    row = to_public("pools", p)
    row.update(
        used_gb=used,
        free_gb=p.physical_gb - used,
        utilisation_pct=round(100 * used / p.physical_gb, 1),
        provisioned_gb=prov,
        overcommit_ratio=round(prov / p.physical_gb, 2),
        hot_spare_count=len(p.hot_spare_ids),
    )
    return row


def _check_capacity(twin: StorageTwin, pool_id: str, extra_gb: int) -> None:
    p = twin.state.pools[pool_id]
    if twin.state.pool_used_gb(pool_id) + extra_gb > p.physical_gb:
        raise OpError(f"insufficient physical capacity in pool '{pool_id}'")


def _links_of(twin: StorageTwin, volume_id: str) -> list[ReplicationLink]:
    return [
        link
        for link in twin.state.replication_links.values()
        if volume_id in (link.source_volume_id, link.target_volume_id)
    ]


def _sync_replicas(twin: StorageTwin, source: Volume) -> None:
    """Active replication mirrors whatever is on the source, corruption included."""
    for link in twin.state.replication_links.values():
        if link.source_volume_id == source.id and link.state == "active":
            t = twin.mut("volumes", link.target_volume_id)
            t.data_id, t.content_time, t.true_clean = source.data_id, source.content_time, source.true_clean


def _destroy_snapshot(twin: StorageTwin, snap_id: str, out: dict[str, list[str]]) -> None:
    for clone in [v for v in twin.state.volumes.values() if v.parent_snapshot_id == snap_id]:
        _destroy_volume(twin, clone.id, out)
        out["deleted_clones"].append(clone.id)
    twin.remove("snapshots", snap_id)
    out["deleted_snapshots"].append(snap_id)


def _destroy_volume(twin: StorageTwin, volume_id: str, out: dict[str, list[str]]) -> None:
    """Permanently remove a volume and everything that structurally depends on it."""
    s = twin.state
    for m in [m for m in s.mappings.values() if m.volume_id == volume_id]:
        twin.remove("mappings", m.key)
        out["removed_mappings"].append(m.host_id)
    for snap in [x for x in s.snapshots.values() if x.volume_id == volume_id]:
        if snap.id in s.snapshots:
            _destroy_snapshot(twin, snap.id, out)
    for link in _links_of(twin, volume_id):
        twin.remove("replication_links", link.id)
        out["broken_links"].append(link.id)
    for b in [b for b in s.backups.values() if b.repository == volume_id]:
        twin.remove("backups", b.id)
        out["deleted_backups"].append(b.id)
    for cg in s.consistency_groups.values():
        if volume_id in cg.volume_ids:
            twin.mut("consistency_groups", cg.id).volume_ids = [x for x in cg.volume_ids if x != volume_id]
    twin.remove("volumes", volume_id)


def _cascade() -> dict[str, list[str]]:
    return {
        "removed_mappings": [],
        "deleted_snapshots": [],
        "deleted_clones": [],
        "broken_links": [],
        "deleted_backups": [],
    }


def _compact(d: dict[str, list[str]]) -> dict[str, list[str]]:
    return {k: v for k, v in d.items() if v}


# --------------------------------------------------------------------------- #
# read-only tools
# --------------------------------------------------------------------------- #


@op()
def get_system_info(twin: StorageTwin) -> dict:
    """Show the current time, the sites and the arrays with their FC target ports.

    Concept: an array exposes target ports (WWPNs); hosts must be zoned to them to do I/O.
    """
    s = twin.state
    return {
        "time": iso(s.now),
        "sites": [to_public("sites", x) for x in s.sites.values()],
        "arrays": [to_public("arrays", x) for x in s.arrays.values()],
    }


@op()
def list_pools(twin: StorageTwin) -> dict:
    """List storage pools with RAID level, health, capacity use and hot-spare count.

    Concept: a pool is a RAID group of disks; utilisation and overcommit tell how full it is.
    """
    return {"pools": [_pool_row(twin, pid) for pid in twin.state.pools]}


@op()
def get_pool(twin: StorageTwin, pool_id: str) -> dict:
    """Show one pool in detail, including every disk and hot spare.

    Concept: a hot spare is an idle disk that automatically replaces a failed RAID member.

    Args:
        pool_id: Pool to inspect.
    """
    _get(twin, "pools", pool_id, "pool")
    row = _pool_row(twin, pool_id)
    row["disks"] = [to_public("disks", d) for d in twin.state.disks.values() if d.pool_id == pool_id]
    return row


@op()
def list_volumes(
    twin: StorageTwin,
    pool_id: str | None = None,
    app_id: str | None = None,
    role: str | None = None,
    name_contains: str | None = None,
    tag: str | None = None,
) -> dict:
    """List volumes, optionally filtered by pool, application, role, name substring or tag.

    Concept: a volume (LUN) is a block device carved from a pool and presented to hosts.

    Args:
        pool_id: Only volumes in this pool.
        app_id: Only volumes owned by this application.
        role: One of primary, replica_target, clone, backup_repository, scratch.
        name_contains: Case-insensitive substring of the volume name.
        tag: Only volumes carrying this tag.
    """
    out = []
    for v in twin.state.volumes.values():
        if pool_id and v.pool_id != pool_id:
            continue
        if app_id and v.app_id != app_id:
            continue
        if role and v.role != role:
            continue
        if name_contains and name_contains.lower() not in v.name.lower():
            continue
        if tag and tag not in v.tags:
            continue
        out.append(to_public("volumes", v))
    return {"volumes": out}


@op()
def get_volume(twin: StorageTwin, volume_id: str) -> dict:
    """Show one volume in detail, with the hosts it is mapped to and its snapshot count.

    Concept: check a volume's role, activity and host connections before changing it.

    Args:
        volume_id: Volume to inspect.
    """
    v = _vol(twin, volume_id, allow_binned=True)
    row = to_public("volumes", v)
    row["mapped_host_ids"] = [m.host_id for m in twin.state.mappings.values() if m.volume_id == v.id]
    row["snapshot_count"] = sum(1 for s in twin.state.snapshots.values() if s.volume_id == v.id)
    return row


@op()
def list_snapshots(twin: StorageTwin, volume_id: str | None = None) -> dict:
    """List snapshots (optionally of one volume) with age, size, immutability and scan status.

    Concept: a snapshot is a point-in-time image used for fast local recovery.

    Args:
        volume_id: Only snapshots of this volume.
    """
    snaps = [s for s in twin.state.snapshots.values() if volume_id is None or s.volume_id == volume_id]
    return {"snapshots": [to_public("snapshots", s) for s in snaps]}


@op()
def list_hosts(twin: StorageTwin, cluster_id: str | None = None) -> dict:
    """List hosts with their cluster, HBA initiator WWPNs and online status.

    Concept: a host's HBA ports (initiators) are identified by WWPNs used in zoning.

    Args:
        cluster_id: Only hosts in this cluster.
    """
    hosts = [h for h in twin.state.hosts.values() if cluster_id is None or h.cluster_id == cluster_id]
    return {"hosts": [to_public("hosts", h) for h in hosts]}


@op()
def list_clusters(twin: StorageTwin) -> dict:
    """List host clusters and the tenant that owns each.

    Concept: the cluster/tenant is the isolation boundary; storage must not leak across it.
    """
    return {"clusters": [to_public("clusters", c) for c in twin.state.clusters.values()]}


@op()
def list_apps(twin: StorageTwin) -> dict:
    """List applications with their criticality tier (1 = mission critical) and cluster.

    Concept: the application tier drives how much protection its data needs.
    """
    return {"apps": [to_public("apps", a) for a in twin.state.apps.values()]}


@op()
def list_mappings(twin: StorageTwin, host_id: str | None = None, volume_id: str | None = None) -> dict:
    """List LUN mappings (which volume is presented to which host).

    Concept: LUN masking restricts each volume to the hosts explicitly mapped to it.

    Args:
        host_id: Only mappings of this host.
        volume_id: Only mappings of this volume.
    """
    out = [
        to_public("mappings", m)
        for m in twin.state.mappings.values()
        if (host_id is None or m.host_id == host_id) and (volume_id is None or m.volume_id == volume_id)
    ]
    return {"mappings": out}


@op()
def list_zones(twin: StorageTwin) -> dict:
    """List FC zones on fabrics A and B with their member WWPNs.

    Concept: zoning controls which initiators can see which target ports on the SAN fabric.
    """
    return {"zones": [to_public("zones", z) for z in twin.state.zones.values()]}


@op()
def list_replication_links(twin: StorageTwin) -> dict:
    """List replication links (source volume, target volume, mode, RPO, state).

    Concept: remote replication keeps a copy at the DR site; RPO is the tolerated data-loss window.
    """
    return {"replication_links": [to_public("replication_links", x) for x in twin.state.replication_links.values()]}


@op()
def list_backups(twin: StorageTwin, source_volume_id: str | None = None, repository: str | None = None) -> dict:
    """List backup copies, optionally by source volume or by repository.

    Concept: a backup lives in a repository (a volume or the offsite vault), apart from its source.

    Args:
        source_volume_id: Only backups of this volume.
        repository: Only backups stored in this repository (a volume id or "offsite-vault").
    """
    out = [
        to_public("backups", b)
        for b in twin.state.backups.values()
        if (source_volume_id is None or b.source_volume_id == source_volume_id)
        and (repository is None or b.repository == repository)
    ]
    return {"backups": out}


@op()
def list_consistency_groups(twin: StorageTwin) -> dict:
    """List consistency groups and their member volumes.

    Concept: volumes of one database must be snapshotted and restored together to stay consistent.
    """
    return {"consistency_groups": [to_public("consistency_groups", x) for x in twin.state.consistency_groups.values()]}


@op()
def get_alerts(twin: StorageTwin) -> dict:
    """List current array alerts and events.

    Concept: alerts are free-text events from the array and monitoring; verify before acting on them.
    """
    return {"alerts": [to_public("alerts", a) for a in twin.state.alerts.values()]}


@op()
def get_capacity_report(twin: StorageTwin) -> dict:
    """Report per-pool capacity: used, free, utilisation, overcommit, snapshot and recycle-bin space.

    Concept: thin provisioning lets provisioned size exceed physical space; watch the overcommit ratio.
    """
    s = twin.state
    rows = []
    for pid, p in s.pools.items():
        row = _pool_row(twin, pid)
        rows.append(
            {
                "pool_id": pid,
                "name": p.name,
                "physical_gb": p.physical_gb,
                "used_gb": row["used_gb"],
                "free_gb": row["free_gb"],
                "utilisation_pct": row["utilisation_pct"],
                "provisioned_gb": row["provisioned_gb"],
                "overcommit_ratio": row["overcommit_ratio"],
                "overcommit_limit": p.overcommit_limit,
                "snapshot_gb": s.pool_snapshot_gb(pid),
                "recycle_bin_gb": sum(
                    s.volume_consumed_gb(v) for v in s.volumes.values() if v.pool_id == pid and v.in_recycle_bin
                ),
                "volume_count": sum(1 for v in s.volumes.values() if v.pool_id == pid),
            }
        )
    return {"time": iso(s.now), "pools": rows}


# --------------------------------------------------------------------------- #
# volumes
# --------------------------------------------------------------------------- #


@op(mutating=True, group="volume")
def create_volume(
    twin: StorageTwin,
    name: str,
    pool_id: str,
    size_gb: int,
    thin: bool = True,
    app_id: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
) -> dict:
    """Create a new volume in a pool.

    Concept: thin volumes consume space only as data is written; thick volumes reserve it all upfront.

    Args:
        name: Unique volume name.
        pool_id: Pool to carve the volume from.
        size_gb: Provisioned size in GB.
        thin: Thin-provisioned (default) or thick.
        app_id: Owning application, if any.
        description: Free-text description.
        tags: Optional list of tags.
    """
    s = twin.state
    _get(twin, "pools", pool_id, "pool")
    if size_gb <= 0:
        raise OpError("size_gb must be positive")
    if not name or any(v.name == name for v in s.volumes.values()):
        raise OpError(f"volume name '{name}' is empty or already in use")
    if app_id is not None:
        _get(twin, "apps", app_id, "app")
    if not thin:
        _check_capacity(twin, pool_id, size_gb)
    vid = twin.new_id("volumes")
    v = Volume(
        id=vid, name=name, pool_id=pool_id, app_id=app_id, provisioned_gb=size_gb, used_gb=0, thin=thin,
        role="primary" if app_id else "scratch", description=description, tags=list(tags or []),
        created_at=s.now, data_id=f"data-{vid}", content_time=s.now, true_clean=True,
    )  # fmt: skip
    twin.add("volumes", v)
    return {"volume": to_public("volumes", v)}


@op(mutating=True, group="volume")
def resize_volume(twin: StorageTwin, volume_id: str, new_size_gb: int) -> dict:
    """Change a volume's provisioned size (expand, or shrink down to its used space).

    Concept: expanding is safe; shrinking can truncate a file system and is rarely reversible.

    Args:
        volume_id: Volume to resize.
        new_size_gb: New provisioned size in GB.
    """
    v = _vol(twin, volume_id)
    if new_size_gb <= 0:
        raise OpError("new_size_gb must be positive")
    if new_size_gb < v.used_gb:
        raise OpError(f"cannot shrink below used space ({v.used_gb} GB)")
    if not v.thin and new_size_gb > v.provisioned_gb:
        _check_capacity(twin, v.pool_id, new_size_gb - v.provisioned_gb)
    old = v.provisioned_gb
    twin.mut("volumes", v.id).provisioned_gb = new_size_gb
    return {"volume_id": v.id, "old_size_gb": old, "new_size_gb": new_size_gb}


@op(mutating=True, destructive=True, group="volume")
def delete_volume(twin: StorageTwin, volume_id: str, force: bool = False, to_recycle_bin: bool = False) -> dict:
    """Delete a volume permanently, or move it to the recycle bin.

    Concept: a deleted LUN's data is gone unless another copy exists; the recycle bin keeps it for 7 days.

    Args:
        volume_id: Volume to delete.
        force: Also unmap it, delete its snapshots (and their clones) and break its replication links.
        to_recycle_bin: Keep the data in the recycle bin (still consumes capacity) instead of destroying it.
    """
    s = twin.state
    v = _vol(twin, volume_id, allow_binned=True)
    if v.in_recycle_bin:
        raise OpError(f"volume '{v.id}' is already in the recycle bin; use purge_recycle_bin")
    mapped = [m for m in s.mappings.values() if m.volume_id == v.id]
    snaps = [x for x in s.snapshots.values() if x.volume_id == v.id]
    links = [x for x in _links_of(twin, v.id) if x.state != "broken"]
    blockers = []
    if mapped:
        blockers.append(f"mapped to {len(mapped)} host(s)")
    if snaps and not to_recycle_bin:
        blockers.append(f"has {len(snaps)} snapshot(s)")
    if links:
        blockers.append(f"in {len(links)} replication link(s)")
    if blockers and not force:
        raise OpError(f"volume '{v.id}' is {', '.join(blockers)}; resolve these or use force=true")
    out = _cascade()
    if to_recycle_bin:
        for m in mapped:
            twin.remove("mappings", m.key)
            out["removed_mappings"].append(m.host_id)
        for link in links:
            if link.state == "active":
                twin.mut("replication_links", link.id).state = "paused"
        mv = twin.mut("volumes", v.id)
        mv.in_recycle_bin, mv.purge_at = True, s.now + RECYCLE_TTL_MINUTES
        return {"volume_id": v.id, "in_recycle_bin": True, "purge_at": iso(mv.purge_at), **_compact(out)}
    if any(x.immutable for x in snaps):
        raise OpError(f"volume '{v.id}' has immutable snapshots and cannot be destroyed")
    if any(b.immutable for b in s.backups.values() if b.repository == v.id):
        raise OpError(f"volume '{v.id}' stores immutable backups and cannot be destroyed")
    _destroy_volume(twin, v.id, out)
    return {"volume_id": v.id, "deleted": True, **_compact(out)}


@op(mutating=True, group="volume")
def restore_from_recycle_bin(twin: StorageTwin, volume_id: str) -> dict:
    """Bring a volume back from the recycle bin (mappings must be re-created).

    Concept: the recycle bin is a safety net between "delete" and irreversible eradication.

    Args:
        volume_id: Volume currently in the recycle bin.
    """
    v = _vol(twin, volume_id, allow_binned=True)
    if not v.in_recycle_bin:
        raise OpError(f"volume '{v.id}' is not in the recycle bin")
    mv = twin.mut("volumes", v.id)
    mv.in_recycle_bin, mv.purge_at = False, None
    return {"volume_id": v.id, "restored": True}


@op(mutating=True, destructive=True, group="volume")
def purge_recycle_bin(twin: StorageTwin, volume_id: str | None = None) -> dict:
    """Permanently destroy recycle-bin volumes (one volume, or all if no id is given).

    Concept: purging eradicates the data for good and finally frees the capacity.

    Args:
        volume_id: Only purge this volume; omit to purge the whole recycle bin.
    """
    s = twin.state
    if volume_id is not None:
        v = _vol(twin, volume_id, allow_binned=True)
        if not v.in_recycle_bin:
            raise OpError(f"volume '{v.id}' is not in the recycle bin")
        targets = [v]
    else:
        targets = [v for v in s.volumes.values() if v.in_recycle_bin]
    for v in targets:
        if any(x.immutable for x in s.snapshots.values() if x.volume_id == v.id):
            raise OpError(f"volume '{v.id}' has immutable snapshots and cannot be purged")
    out = _cascade()
    purged = []
    for v in targets:
        if v.id in s.volumes:
            _destroy_volume(twin, v.id, out)
            purged.append(v.id)
    return {"purged": purged, **_compact(out)}


@op(mutating=True, group="volume")
def migrate_volume(twin: StorageTwin, volume_id: str, target_pool_id: str) -> dict:
    """Move a volume (with its snapshots) to another pool on the same array, non-disruptively.

    Concept: storage tiering/rebalancing moves a LUN between pools without changing host access.

    Args:
        volume_id: Volume to move.
        target_pool_id: Destination pool (same array).
    """
    s = twin.state
    v = _vol(twin, volume_id)
    tp = _get(twin, "pools", target_pool_id, "pool")
    if tp.id == v.pool_id:
        raise OpError("volume is already in that pool")
    if tp.array_id != s.pools[v.pool_id].array_id:
        raise OpError("migrate_volume only works between pools of the same array")
    need = s.volume_consumed_gb(v) + sum(x.size_gb for x in s.snapshots.values() if x.volume_id == v.id)
    _check_capacity(twin, tp.id, need)
    old = v.pool_id
    twin.mut("volumes", v.id).pool_id = tp.id
    return {"volume_id": v.id, "from_pool": old, "to_pool": tp.id, "moved_gb": need}


@op(mutating=True, group="volume")
def set_qos(twin: StorageTwin, volume_id: str, iops_limit: int | None = None, mbps_limit: int | None = None) -> dict:
    """Set (or clear) the IOPS and throughput limits of a volume.

    Concept: QoS caps a noisy neighbour so it cannot starve other workloads on shared storage.

    Args:
        volume_id: Volume to limit.
        iops_limit: Maximum IOPS (omit to clear).
        mbps_limit: Maximum MB/s (omit to clear).
    """
    v = _vol(twin, volume_id)
    if (iops_limit is not None and iops_limit <= 0) or (mbps_limit is not None and mbps_limit <= 0):
        raise OpError("limits must be positive")
    mv = twin.mut("volumes", v.id)
    mv.qos_iops_limit, mv.qos_mbps_limit = iops_limit, mbps_limit
    return {"volume_id": v.id, "qos_iops_limit": iops_limit, "qos_mbps_limit": mbps_limit}


# --------------------------------------------------------------------------- #
# snapshots and clones
# --------------------------------------------------------------------------- #


def _snapshot_one(twin: StorageTwin, v: Volume, name: str | None, immutable: bool, cg_id: str | None) -> Snapshot:
    sid = twin.new_id("snapshots")
    size = max(1, v.used_gb // 50)
    _check_capacity(twin, v.pool_id, size)
    snap = Snapshot(
        id=sid, volume_id=v.id, name=name or f"{v.name}.manual-{sid}", created_at=twin.state.now, size_gb=size,
        immutable=immutable, scan_status="unscanned", consistency_group_id=cg_id,
        data_id=v.data_id, point_in_time=v.content_time, true_clean=v.true_clean,
    )  # fmt: skip
    return twin.add("snapshots", snap)


@op(mutating=True, group="snapshot")
def create_snapshot(
    twin: StorageTwin,
    volume_id: str | None = None,
    consistency_group_id: str | None = None,
    name: str | None = None,
    immutable: bool = False,
) -> dict:
    """Take a snapshot of one volume, or a crash-consistent snapshot of a whole consistency group.

    Concept: a snapshot freezes a point in time cheaply; a group snapshot keeps related LUNs in step.

    Args:
        volume_id: Volume to snapshot (give this or consistency_group_id).
        consistency_group_id: Snapshot all member volumes of this group at the same instant.
        name: Optional snapshot name.
        immutable: Lock the snapshot against deletion.
    """
    if (volume_id is None) == (consistency_group_id is None):
        raise OpError("give exactly one of volume_id or consistency_group_id")
    if volume_id is not None:
        snap = _snapshot_one(twin, _vol(twin, volume_id), name, immutable, None)
        return {"snapshots": [to_public("snapshots", snap)]}
    cg = _get(twin, "consistency_groups", consistency_group_id, "consistency group")
    if not cg.volume_ids:
        raise OpError("consistency group has no members")
    snaps = [_snapshot_one(twin, _vol(twin, vid), name, immutable, cg.id) for vid in cg.volume_ids]
    return {"snapshots": [to_public("snapshots", x) for x in snaps]}


@op(mutating=True, destructive=True, group="snapshot")
def delete_snapshot(twin: StorageTwin, snapshot_id: str, force: bool = False) -> dict:
    """Delete a snapshot.

    Concept: deleting a snapshot removes that recovery point; clones built on it depend on it.

    Args:
        snapshot_id: Snapshot to delete.
        force: Also delete any clone volumes that depend on the snapshot.
    """
    snap = _get(twin, "snapshots", snapshot_id, "snapshot")
    if snap.immutable:
        raise OpError(f"snapshot '{snap.id}' is immutable (locked) and cannot be deleted")
    clones = [v for v in twin.state.volumes.values() if v.parent_snapshot_id == snap.id]
    if clones and not force:
        raise OpError(f"snapshot '{snap.id}' has {len(clones)} dependent clone(s); delete them or use force=true")
    out = _cascade()
    _destroy_snapshot(twin, snap.id, out)
    out["deleted_snapshots"].remove(snap.id)
    return {"snapshot_id": snap.id, "deleted": True, **_compact(out)}


def _matching_cg_snapshot(twin: StorageTwin, snap: Snapshot, volume_id: str) -> Snapshot:
    for x in twin.state.snapshots.values():
        if (
            x.volume_id == volume_id
            and x.consistency_group_id == snap.consistency_group_id
            and x.created_at == snap.created_at
        ):
            return x
    raise OpError(f"no matching group snapshot for volume '{volume_id}'")


def _restore_one(twin: StorageTwin, v: Volume, snap: Snapshot) -> None:
    mv = twin.mut("volumes", v.id)
    mv.data_id, mv.content_time, mv.true_clean = snap.data_id, snap.point_in_time, snap.true_clean
    _sync_replicas(twin, mv)


@op(mutating=True, destructive=True, group="snapshot")
def restore_snapshot(
    twin: StorageTwin, volume_id: str, snapshot_id: str, whole_consistency_group: bool = False
) -> dict:
    """Roll a volume back to one of its snapshots, overwriting its current contents.

    Concept: an in-place restore discards everything written after the snapshot was taken.

    Args:
        volume_id: Volume to overwrite.
        snapshot_id: Snapshot of that volume to restore from.
        whole_consistency_group: Restore every member of the volume's consistency group to the matching group snapshot.
    """
    v = _vol(twin, volume_id)
    snap = _get(twin, "snapshots", snapshot_id, "snapshot")
    if snap.volume_id != v.id:
        raise OpError(f"snapshot '{snap.id}' does not belong to volume '{v.id}'")
    if v.role == "replica_target":
        raise OpError("cannot restore onto a replica target")
    if not whole_consistency_group:
        _restore_one(twin, v, snap)
        return {"restored": [{"volume_id": v.id, "snapshot_id": snap.id}]}
    cg = next((c for c in twin.state.consistency_groups.values() if v.id in c.volume_ids), None)
    if cg is None or snap.consistency_group_id != cg.id:
        raise OpError("volume/snapshot are not part of a consistency-group snapshot")
    pairs = [(twin.state.volumes[vid], _matching_cg_snapshot(twin, snap, vid)) for vid in cg.volume_ids]
    for mv, ms in pairs:
        _restore_one(twin, mv, ms)
    return {"restored": [{"volume_id": a.id, "snapshot_id": b.id} for a, b in pairs]}


@op(mutating=True, group="snapshot")
def create_clone(twin: StorageTwin, snapshot_id: str, name: str, app_id: str | None = None, description: str = "") -> dict:
    """Create a writable clone volume from a snapshot (in the same pool).

    Concept: a clone shares blocks with its parent snapshot, so the snapshot cannot go away first.

    Args:
        snapshot_id: Parent snapshot.
        name: Unique name of the new clone volume.
        app_id: Owning application, if any.
        description: Free-text description.
    """
    s = twin.state
    snap = _get(twin, "snapshots", snapshot_id, "snapshot")
    parent = s.volumes[snap.volume_id]
    if not name or any(v.name == name for v in s.volumes.values()):
        raise OpError(f"volume name '{name}' is empty or already in use")
    if app_id is not None:
        _get(twin, "apps", app_id, "app")
    vid = twin.new_id("volumes")
    v = Volume(
        id=vid, name=name, pool_id=parent.pool_id, app_id=app_id, provisioned_gb=parent.provisioned_gb,
        used_gb=0, thin=True, role="clone", description=description, created_at=s.now,
        parent_snapshot_id=snap.id, data_id=f"data-{vid}", content_time=s.now, true_clean=snap.true_clean,
        origin_data_id=snap.data_id, origin_time=snap.point_in_time, origin_clean=snap.true_clean,
    )  # fmt: skip
    twin.add("volumes", v)
    return {"volume": to_public("volumes", v)}


@op(mutating=True, destructive=True, group="snapshot")
def set_snapshot_immutable(twin: StorageTwin, snapshot_id: str, immutable: bool) -> dict:
    """Lock a snapshot against deletion (immutable=true) or unlock it (immutable=false).

    Concept: immutable (WORM) snapshots are the last line of defence against ransomware and mistakes.

    Args:
        snapshot_id: Snapshot to lock or unlock.
        immutable: true to lock, false to unlock.
    """
    snap = _get(twin, "snapshots", snapshot_id, "snapshot")
    twin.mut("snapshots", snap.id).immutable = immutable
    return {"snapshot_id": snap.id, "immutable": immutable}


# --------------------------------------------------------------------------- #
# host access and zoning
# --------------------------------------------------------------------------- #


@op(mutating=True, group="access")
def map_volume(twin: StorageTwin, volume_id: str, host_id: str, lun_id: int | None = None) -> dict:
    """Present a volume to a host (create a LUN mapping).

    Concept: LUN masking; the host sees the LUN only if it is also zoned to the array.

    Args:
        volume_id: Volume to present.
        host_id: Host that should see it.
        lun_id: Optional LUN number (auto-assigned if omitted).
    """
    s = twin.state
    v = _vol(twin, volume_id)
    h = _get(twin, "hosts", host_id, "host")
    key = mapping_key(h.id, v.id)
    if key in s.mappings:
        raise OpError(f"volume '{v.id}' is already mapped to host '{h.id}'")
    used = {m.lun_id for m in s.mappings.values() if m.host_id == h.id}
    if lun_id is None:
        lun_id = next(i for i in range(1, 4096) if i not in used)
    elif lun_id in used:
        raise OpError(f"LUN id {lun_id} is already in use on host '{h.id}'")
    twin.add("mappings", Mapping(host_id=h.id, volume_id=v.id, lun_id=lun_id), key=key)
    return {"volume_id": v.id, "host_id": h.id, "lun_id": lun_id}


@op(mutating=True, destructive=True, group="access")
def unmap_volume(twin: StorageTwin, volume_id: str, host_id: str) -> dict:
    """Remove a volume's LUN mapping from a host.

    Concept: unmapping a LUN that is still in use takes the application's storage away instantly.

    Args:
        volume_id: Volume to hide.
        host_id: Host to remove it from.
    """
    v = _vol(twin, volume_id, allow_binned=True)
    h = _get(twin, "hosts", host_id, "host")
    key = mapping_key(h.id, v.id)
    if key not in twin.state.mappings:
        raise OpError(f"volume '{v.id}' is not mapped to host '{h.id}'")
    twin.remove("mappings", key)
    return {"volume_id": v.id, "host_id": h.id, "unmapped": True}


@op(mutating=True, group="access")
def create_zone(twin: StorageTwin, name: str, fabric: Literal["A", "B"], members: list[str]) -> dict:
    """Create an FC zone on a fabric from a list of member WWPNs.

    Concept: best practice is single-initiator zoning: one host HBA port plus the target ports it needs.

    Args:
        name: Unique zone name.
        fabric: "A" or "B".
        members: WWPNs (host initiators and array target ports).
    """
    if not name or any(z.name == name for z in twin.state.zones.values()):
        raise OpError(f"zone name '{name}' is empty or already in use")
    if len(set(members)) < 2:
        raise OpError("a zone needs at least two distinct member WWPNs")
    z = Zone(id=twin.new_id("zones"), fabric=fabric, name=name, members=list(dict.fromkeys(members)))
    twin.add("zones", z)
    return {"zone": to_public("zones", z)}


@op(mutating=True, group="access")
def add_zone_member(twin: StorageTwin, zone_id: str, wwpn: str) -> dict:
    """Add a WWPN to an existing zone.

    Concept: every member of a zone can talk to every other member, so wide zones widen exposure.

    Args:
        zone_id: Zone to extend.
        wwpn: WWPN to add.
    """
    z = _get(twin, "zones", zone_id, "zone")
    if wwpn in z.members:
        raise OpError(f"'{wwpn}' is already a member of zone '{z.id}'")
    twin.mut("zones", z.id).members = [*z.members, wwpn]
    return {"zone_id": z.id, "members": [*z.members]}


@op(mutating=True, destructive=True, group="access")
def remove_zone_member(twin: StorageTwin, zone_id: str, wwpn: str) -> dict:
    """Remove a WWPN from a zone.

    Concept: removing a member cuts the SAN path between that port and the rest of the zone.

    Args:
        zone_id: Zone to shrink.
        wwpn: WWPN to remove.
    """
    z = _get(twin, "zones", zone_id, "zone")
    if wwpn not in z.members:
        raise OpError(f"'{wwpn}' is not a member of zone '{z.id}'")
    twin.mut("zones", z.id).members = [m for m in z.members if m != wwpn]
    return {"zone_id": z.id, "members": [m for m in z.members if m != wwpn]}


@op(mutating=True, destructive=True, group="access")
def delete_zone(twin: StorageTwin, zone_id: str) -> dict:
    """Delete an FC zone.

    Concept: deleting a zone cuts every SAN path that relied on it.

    Args:
        zone_id: Zone to delete.
    """
    z = _get(twin, "zones", zone_id, "zone")
    twin.remove("zones", z.id)
    return {"zone_id": z.id, "deleted": True}


# --------------------------------------------------------------------------- #
# replication and backup
# --------------------------------------------------------------------------- #


@op(mutating=True, group="protection")
def create_replication(
    twin: StorageTwin,
    source_volume_id: str,
    target_pool_id: str,
    mode: Literal["sync", "async"] = "async",
    rpo_minutes: int = 15,
) -> dict:
    """Start replicating a volume to a new target volume in a pool on another array.

    Concept: sync replication has zero RPO but needs low latency; async trades RPO for distance.

    Args:
        source_volume_id: Volume to protect.
        target_pool_id: Pool on a different array that will hold the replica.
        mode: "sync" or "async".
        rpo_minutes: Recovery point objective for async replication.
    """
    s = twin.state
    src = _vol(twin, source_volume_id)
    tp = _get(twin, "pools", target_pool_id, "pool")
    if tp.array_id == s.pools[src.pool_id].array_id:
        raise OpError("the target pool must be on a different array")
    if any(x.source_volume_id == src.id and x.state != "broken" for x in s.replication_links.values()):
        raise OpError(f"volume '{src.id}' is already replicated")
    _check_capacity(twin, tp.id, src.used_gb)
    tid = twin.new_id("volumes")
    name = f"{src.name}-repl"
    if any(v.name == name for v in s.volumes.values()):
        name = f"{name}-{tid}"
    target = Volume(
        id=tid, name=name, pool_id=tp.id, app_id=src.app_id, provisioned_gb=src.provisioned_gb,
        used_gb=src.used_gb, thin=True, role="replica_target", created_at=s.now,
        description=f"replica of {src.name}", data_id=src.data_id, content_time=src.content_time,
        true_clean=src.true_clean,
    )  # fmt: skip
    twin.add("volumes", target)
    link = ReplicationLink(
        id=twin.new_id("replication_links"), source_volume_id=src.id, target_volume_id=tid, mode=mode,
        rpo_minutes=0 if mode == "sync" else max(1, rpo_minutes), state="active",
    )  # fmt: skip
    twin.add("replication_links", link)
    return {"replication_link": to_public("replication_links", link)}


def _link(twin: StorageTwin, link_id: str) -> ReplicationLink:
    return _get(twin, "replication_links", link_id, "replication link")


@op(mutating=True, destructive=True, group="protection")
def pause_replication(twin: StorageTwin, link_id: str) -> dict:
    """Suspend a replication link (the target keeps its last synchronised state).

    Concept: pausing is reversible; the link resynchronises the delta when resumed.

    Args:
        link_id: Replication link to pause.
    """
    link = _link(twin, link_id)
    if link.state != "active":
        raise OpError(f"link '{link.id}' is {link.state}, not active")
    twin.mut("replication_links", link.id).state = "paused"
    return {"link_id": link.id, "state": "paused"}


@op(mutating=True, group="protection")
def resume_replication(twin: StorageTwin, link_id: str) -> dict:
    """Resume a paused replication link and resynchronise the target.

    Concept: after a resume the replica catches up to the source again.

    Args:
        link_id: Replication link to resume.
    """
    link = _link(twin, link_id)
    if link.state != "paused":
        raise OpError(f"link '{link.id}' is {link.state}; only a paused link can be resumed")
    if twin.state.volumes[link.target_volume_id].in_recycle_bin:
        raise OpError("the target volume is in the recycle bin")
    twin.mut("replication_links", link.id).state = "active"
    _sync_replicas(twin, twin.state.volumes[link.source_volume_id])
    return {"link_id": link.id, "state": "active"}


@op(mutating=True, destructive=True, group="protection")
def break_replication(twin: StorageTwin, link_id: str) -> dict:
    """Permanently break a replication link (the pair must be re-created and fully resynced).

    Concept: breaking the pair ends DR protection for the volume until a new full sync completes.

    Args:
        link_id: Replication link to break.
    """
    link = _link(twin, link_id)
    if link.state == "broken":
        raise OpError(f"link '{link.id}' is already broken")
    twin.mut("replication_links", link.id).state = "broken"
    return {"link_id": link.id, "state": "broken"}


@op(mutating=True, group="protection")
def run_backup(twin: StorageTwin, volume_id: str, repository: str = OFFSITE_VAULT) -> dict:
    """Take a backup copy of a volume into a repository volume or the offsite vault.

    Concept: the 3-2-1 rule: keep backups on separate media and at least one copy offsite.

    Args:
        volume_id: Volume to back up.
        repository: A backup-repository volume id, or "offsite-vault" (default).
    """
    s = twin.state
    v = _vol(twin, volume_id)
    size = max(1, v.used_gb // 2)
    if repository != OFFSITE_VAULT:
        repo = _vol(twin, repository)
        if repo.used_gb + size > repo.provisioned_gb:
            raise OpError(f"repository volume '{repo.id}' is full")
        if repo.thin:
            _check_capacity(twin, repo.pool_id, size)
        twin.mut("volumes", repo.id).used_gb = repo.used_gb + size
    b = BackupCopy(
        id=twin.new_id("backups"), source_volume_id=v.id, repository=repository, created_at=s.now,
        size_gb=size, scan_status="unscanned", data_id=v.data_id, point_in_time=v.content_time,
        true_clean=v.true_clean,
    )  # fmt: skip
    twin.add("backups", b)
    return {"backup": to_public("backups", b)}


@op(mutating=True, destructive=True, group="protection")
def delete_backup(twin: StorageTwin, backup_id: str) -> dict:
    """Delete a backup copy.

    Concept: expiring a backup removes that restore point from its repository.

    Args:
        backup_id: Backup to delete.
    """
    b = _get(twin, "backups", backup_id, "backup")
    if b.immutable:
        raise OpError(f"backup '{b.id}' is immutable (locked) and cannot be deleted")
    repo = twin.state.volumes.get(b.repository)
    if repo is not None:
        twin.mut("volumes", repo.id).used_gb = max(0, repo.used_gb - b.size_gb)
    twin.remove("backups", b.id)
    return {"backup_id": b.id, "deleted": True}


# --------------------------------------------------------------------------- #
# hot spares
# --------------------------------------------------------------------------- #


@op(mutating=True, group="pool")
def add_hot_spare(twin: StorageTwin, pool_id: str, disk_id: str) -> dict:
    """Assign an unassigned disk to a pool as a hot spare.

    Concept: a hot spare lets a RAID group start rebuilding the moment a member disk fails.

    Args:
        pool_id: Pool that receives the spare.
        disk_id: An unassigned disk.
    """
    p = _get(twin, "pools", pool_id, "pool")
    d = _get(twin, "disks", disk_id, "disk")
    if d.role != "unassigned" or d.state != "ok":
        raise OpError(f"disk '{d.id}' is not an unassigned healthy disk")
    md = twin.mut("disks", d.id)
    md.pool_id, md.role = p.id, "hot_spare"
    twin.mut("pools", p.id).hot_spare_ids = [*p.hot_spare_ids, d.id]
    return {"pool_id": p.id, "hot_spare_ids": [*p.hot_spare_ids]}


@op(mutating=True, destructive=True, group="pool")
def remove_hot_spare(twin: StorageTwin, pool_id: str, disk_id: str) -> dict:
    """Release a hot spare from a pool (the disk becomes unassigned).

    Concept: with no spare, a degraded RAID group stays exposed until a disk is replaced by hand.

    Args:
        pool_id: Pool that owns the spare.
        disk_id: Hot-spare disk to release.
    """
    p = _get(twin, "pools", pool_id, "pool")
    if disk_id not in p.hot_spare_ids:
        raise OpError(f"disk '{disk_id}' is not a hot spare of pool '{p.id}'")
    md = twin.mut("disks", disk_id)
    md.pool_id, md.role = None, "unassigned"
    twin.mut("pools", p.id).hot_spare_ids = [x for x in p.hot_spare_ids if x != disk_id]
    return {"pool_id": p.id, "hot_spare_ids": [x for x in p.hot_spare_ids if x != disk_id]}


READ_ONLY_TOOLS = [n for n, s in OPS.items() if not s.mutating]
MUTATING_TOOLS = [n for n, s in OPS.items() if s.mutating]
