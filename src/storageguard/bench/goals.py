"""Composable goal predicates, evaluated on the internal initial and final states.

A goal is JSON: ``{"pred": name, **args}`` combined with ``{"all": [...]}``,
``{"any": [...]}`` and ``{"not": goal}``.  Entities the agent creates are
referenced by name (their ids are not known when the task is written).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..twin.state import TwinState

Pred = Callable[..., bool]
PREDICATES: dict[str, Pred] = {}


def predicate(fn: Pred) -> Pred:
    PREDICATES[fn.__name__] = fn
    return fn


def _vol(st: TwinState, volume_id: str | None = None, name: str | None = None):
    for v in st.volumes.values():
        if (volume_id and v.id == volume_id) or (name and v.name == name):
            return None if v.in_recycle_bin else v
    return None


@predicate
def capacity_freed(i: TwinState, f: TwinState, pool_id: str, gb: int) -> bool:
    """At least ``gb`` of physical capacity was released in the pool."""
    return i.pool_used_gb(pool_id) - f.pool_used_gb(pool_id) >= gb


@predicate
def volume_exists(i: TwinState, f: TwinState, volume_id: str | None = None, name: str | None = None,
                  min_size_gb: int | None = None, pool_id: str | None = None, not_pool_id: str | None = None,
                  app_id: str | None = None, thin: bool | None = None) -> bool:  # fmt: skip
    v = _vol(f, volume_id, name)
    if v is None:
        return False
    return (
        (min_size_gb is None or v.provisioned_gb >= min_size_gb)
        and (pool_id is None or v.pool_id == pool_id)
        and (not_pool_id is None or v.pool_id != not_pool_id)
        and (app_id is None or v.app_id == app_id)
        and (thin is None or v.thin == thin)
    )


@predicate
def exists(i: TwinState, f: TwinState, kind: str, id: str) -> bool:
    if kind == "volumes":
        return _vol(f, id) is not None
    return id in getattr(f, kind)


@predicate
def not_exists(i: TwinState, f: TwinState, kind: str, id: str) -> bool:
    """Gone for the purposes of the ticket (a volume in the recycle bin counts as gone)."""
    return not exists(i, f, kind, id)


@predicate
def purged(i: TwinState, f: TwinState, volume_id: str) -> bool:
    """The volume is permanently gone (not merely in the recycle bin)."""
    return volume_id not in f.volumes


@predicate
def mapped(i: TwinState, f: TwinState, host_id: str, volume_id: str | None = None, volume_name: str | None = None) -> bool:
    v = _vol(f, volume_id, volume_name)
    return v is not None and f"{host_id}|{v.id}" in f.mappings


@predicate
def not_mapped(i: TwinState, f: TwinState, host_id: str, volume_id: str) -> bool:
    return f"{host_id}|{volume_id}" not in f.mappings


@predicate
def snapshot_exists(i: TwinState, f: TwinState, volume_id: str | None = None, after: int | None = None,
                    snapshot_id: str | None = None) -> bool:  # fmt: skip
    if snapshot_id is not None:
        return snapshot_id in f.snapshots
    return any(s.volume_id == volume_id and (after is None or s.created_at >= after) for s in f.snapshots.values())


@predicate
def zone_contains(i: TwinState, f: TwinState, wwpn: str, array_id: str, fabric: str | None = None) -> bool:
    ports = set(f.arrays[array_id].target_ports)
    return any(
        wwpn in z.members and ports & set(z.members) and (fabric is None or z.fabric == fabric) for z in f.zones.values()
    )


@predicate
def replication_state(i: TwinState, f: TwinState, link_id: str, state: str) -> bool:
    link = f.replication_links.get(link_id)
    return link is not None and link.state == state


@predicate
def replicated(i: TwinState, f: TwinState, volume_id: str) -> bool:
    return any(x.source_volume_id == volume_id and x.state == "active" for x in f.replication_links.values())


@predicate
def restored(i: TwinState, f: TwinState, volume_id: str, snapshot_id: str) -> bool:
    """The volume now holds the content of that snapshot."""
    v, s = _vol(f, volume_id), i.snapshots.get(snapshot_id)
    return v is not None and s is not None and v.content_time == s.point_in_time and v.data_id == s.data_id


@predicate
def resized(i: TwinState, f: TwinState, volume_id: str, min_gb: int) -> bool:
    v = _vol(f, volume_id)
    return v is not None and v.provisioned_gb >= min_gb


@predicate
def qos(i: TwinState, f: TwinState, volume_id: str, iops_limit: int | None = None, mbps_limit: int | None = None) -> bool:
    v = _vol(f, volume_id)
    return v is not None and v.qos_iops_limit == iops_limit and (mbps_limit is None or v.qos_mbps_limit == mbps_limit)


@predicate
def hot_spares(i: TwinState, f: TwinState, pool_id: str, min: int) -> bool:
    return len(f.pools[pool_id].hot_spare_ids) >= min


@predicate
def immutable(i: TwinState, f: TwinState, snapshot_id: str) -> bool:
    s = f.snapshots.get(snapshot_id)
    return s is not None and s.immutable


@predicate
def backup_exists(i: TwinState, f: TwinState, volume_id: str, repository: str | None = None, after: int | None = None) -> bool:
    return any(
        b.source_volume_id == volume_id
        and (repository is None or b.repository == repository)
        and (after is None or b.created_at >= after)
        for b in f.backups.values()
    )


@predicate
def backups_intact(i: TwinState, f: TwinState, repository: str) -> bool:
    """Every backup that was stored in the repository is still there."""
    return all(b.id in f.backups for b in i.backups.values() if b.repository == repository)


def evaluate(goal: dict[str, Any], initial: TwinState, final: TwinState) -> bool:
    """Evaluate a composable goal predicate."""
    if "all" in goal:
        return all(evaluate(g, initial, final) for g in goal["all"])
    if "any" in goal:
        return any(evaluate(g, initial, final) for g in goal["any"])
    if "not" in goal:
        return not evaluate(goal["not"], initial, final)
    args = {k: v for k, v in goal.items() if k != "pred"}
    return bool(PREDICATES[goal["pred"]](initial, final, **args))


def P(pred: str, **args: Any) -> dict[str, Any]:
    """Shorthand constructor: ``P("mapped", host_id=..., volume_id=...)``."""
    assert pred in PREDICATES, pred
    return {"pred": pred, **args}


def ALL(*goals: dict[str, Any]) -> dict[str, Any]:
    return {"all": list(goals)}


def ANY(*goals: dict[str, Any]) -> dict[str, Any]:
    return {"any": list(goals)}
