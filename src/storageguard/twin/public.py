"""The observability boundary: public serialisation models and ``PublicState``.

Agents, the guard, the dependency graph and every baseline see the twin *only*
as the JSON produced here (it is exactly what the read-only tools return).
Internal content fields (``data_id``, ``point_in_time``, ``true_clean`` ...) are
structurally absent from the public models, so they cannot leak by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import cached_property, lru_cache
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from .models import COLLECTIONS, INTERNAL_FIELDS

if TYPE_CHECKING:  # pragma: no cover
    from .state import StorageTwin

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)  # noqa: UP017


@lru_cache(maxsize=65536)
def iso(minutes: int | None) -> str | None:
    """Simulated-clock minutes -> ISO-8601 UTC string (lexicographically sortable)."""
    if minutes is None:
        return None
    return (EPOCH + timedelta(minutes=int(minutes))).strftime("%Y-%m-%dT%H:%MZ")


def parse_iso(text: str) -> int:
    """Inverse of :func:`iso`."""
    dt = datetime.strptime(text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)  # noqa: UP017
    return int((dt - EPOCH).total_seconds() // 60)


class _Pub(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicSite(_Pub):
    id: str
    name: str
    role: Literal["primary", "dr"]


class PublicArray(_Pub):
    id: str
    site_id: str
    name: str
    target_ports: list[str]


class PublicPool(_Pub):
    id: str
    array_id: str
    name: str
    raid_level: str
    physical_gb: int
    disk_ids: list[str]
    hot_spare_ids: list[str]
    state: str
    overcommit_limit: float


class PublicDisk(_Pub):
    id: str
    pool_id: str | None
    role: str
    state: str
    size_gb: int


class PublicCluster(_Pub):
    id: str
    name: str
    tenant: str


class PublicApp(_Pub):
    id: str
    name: str
    tier: int
    cluster_id: str


class PublicHost(_Pub):
    id: str
    name: str
    cluster_id: str
    initiators: list[str]
    status: str


class PublicVolume(_Pub):
    id: str
    name: str
    pool_id: str
    app_id: str | None
    provisioned_gb: int
    used_gb: int
    thin: bool
    role: str
    description: str
    tags: list[str]
    created_at: str
    last_io_at: str | None
    iops_7d: int
    in_recycle_bin: bool
    purge_at: str | None
    parent_snapshot_id: str | None
    qos_iops_limit: int | None
    qos_mbps_limit: int | None


class PublicSnapshot(_Pub):
    id: str
    volume_id: str
    name: str
    created_at: str
    size_gb: int
    immutable: bool
    scan_status: str
    consistency_group_id: str | None


class PublicReplicationLink(_Pub):
    id: str
    source_volume_id: str
    target_volume_id: str
    mode: str
    rpo_minutes: int
    state: str


class PublicConsistencyGroup(_Pub):
    id: str
    name: str
    app_id: str
    volume_ids: list[str]


class PublicBackupCopy(_Pub):
    id: str
    source_volume_id: str
    repository: str
    created_at: str
    size_gb: int
    immutable: bool
    scan_status: str


class PublicMapping(_Pub):
    host_id: str
    volume_id: str
    lun_id: int


class PublicZone(_Pub):
    id: str
    fabric: str
    name: str
    members: list[str]


class PublicAlert(_Pub):
    id: str
    time: str
    severity: str
    message: str


PUBLIC_MODELS: dict[str, type[_Pub]] = {
    "sites": PublicSite,
    "arrays": PublicArray,
    "pools": PublicPool,
    "disks": PublicDisk,
    "clusters": PublicCluster,
    "apps": PublicApp,
    "hosts": PublicHost,
    "volumes": PublicVolume,
    "snapshots": PublicSnapshot,
    "replication_links": PublicReplicationLink,
    "consistency_groups": PublicConsistencyGroup,
    "backups": PublicBackupCopy,
    "mappings": PublicMapping,
    "zones": PublicZone,
    "alerts": PublicAlert,
}

_TIME_FIELDS: dict[str, tuple[str, ...]] = {
    "volumes": ("created_at", "last_io_at", "purge_at"),
    "snapshots": ("created_at",),
    "backups": ("created_at",),
    "alerts": ("time",),
}
_PUBLIC_FIELDS: dict[str, tuple[str, ...]] = {k: tuple(m.model_fields) for k, m in PUBLIC_MODELS.items()}

# Structural guarantee of the observability boundary.
for _kind, _fields in _PUBLIC_FIELDS.items():
    assert not (set(_fields) & INTERNAL_FIELDS.get(_kind, frozenset())), _kind
    assert set(_fields) <= set(COLLECTIONS[_kind].model_fields), _kind


def to_public(kind: str, entity: Any) -> dict[str, Any]:
    """Project an internal entity onto its public JSON (fast path, no validation)."""
    d = entity.__dict__
    out = {f: d[f] for f in _PUBLIC_FIELDS[kind]}
    for f in _TIME_FIELDS.get(kind, ()):
        out[f] = iso(out[f])
    return out


# --------------------------------------------------------------------------- #
# PublicState: everything a public-API client can know, with handy indexes.
# --------------------------------------------------------------------------- #


@dataclass
class PublicState:
    """A full capture of the public read API at one instant.

    The guard, the graph builder and the LLM-judge baseline are written against
    this class only.  All helper properties are derived purely from public JSON.
    """

    time: str = ""
    sites: dict[str, dict] = field(default_factory=dict)
    arrays: dict[str, dict] = field(default_factory=dict)
    pools: dict[str, dict] = field(default_factory=dict)  # includes computed capacity fields
    disks: dict[str, dict] = field(default_factory=dict)
    clusters: dict[str, dict] = field(default_factory=dict)
    apps: dict[str, dict] = field(default_factory=dict)
    hosts: dict[str, dict] = field(default_factory=dict)
    volumes: dict[str, dict] = field(default_factory=dict)
    snapshots: dict[str, dict] = field(default_factory=dict)
    replication_links: dict[str, dict] = field(default_factory=dict)
    consistency_groups: dict[str, dict] = field(default_factory=dict)
    backups: dict[str, dict] = field(default_factory=dict)
    zones: dict[str, dict] = field(default_factory=dict)
    mappings: list[dict] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)

    ENTITY_KINDS = (
        "sites", "arrays", "pools", "disks", "clusters", "apps", "hosts", "volumes",
        "snapshots", "replication_links", "consistency_groups", "backups", "zones",
    )  # fmt: skip

    # ---- lookup ----
    def kind_of(self, entity_id: str) -> str | None:
        for kind in self.ENTITY_KINDS:
            if entity_id in getattr(self, kind):
                return kind
        return None

    def get(self, entity_id: str) -> dict | None:
        kind = self.kind_of(entity_id)
        return getattr(self, kind)[entity_id] if kind else None

    def label(self, entity_id: str) -> str:
        e = self.get(entity_id)
        if e and e.get("name"):
            return f"{entity_id} ({e['name']})"
        return entity_id

    # ---- indexes ----
    @cached_property
    def snapshots_by_volume(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for s in self.snapshots.values():
            out.setdefault(s["volume_id"], []).append(s)
        return out

    @cached_property
    def clones_by_snapshot(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for v in self.volumes.values():
            if v.get("parent_snapshot_id"):
                out.setdefault(v["parent_snapshot_id"], []).append(v)
        return out

    @cached_property
    def backups_by_source(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for b in self.backups.values():
            out.setdefault(b["source_volume_id"], []).append(b)
        return out

    @cached_property
    def backups_by_repository(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for b in self.backups.values():
            out.setdefault(b["repository"], []).append(b)
        return out

    @cached_property
    def links_by_volume(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for link in self.replication_links.values():
            out.setdefault(link["source_volume_id"], []).append(link)
            out.setdefault(link["target_volume_id"], []).append(link)
        return out

    @cached_property
    def mappings_by_volume(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for m in self.mappings:
            out.setdefault(m["volume_id"], []).append(m)
        return out

    @cached_property
    def mappings_by_host(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for m in self.mappings:
            out.setdefault(m["host_id"], []).append(m)
        return out

    @cached_property
    def cg_by_volume(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for cg in self.consistency_groups.values():
            for vid in cg.get("volume_ids", []):
                out[vid] = cg
        return out

    @cached_property
    def host_array_zoned(self) -> set[tuple[str, str]]:
        """(host_id, array_id) pairs with at least one common zone (any fabric)."""
        port_owner: dict[str, str] = {}
        for a in self.arrays.values():
            for p in a.get("target_ports", []):
                port_owner[p] = a["id"]
        init_owner: dict[str, str] = {}
        for h in self.hosts.values():
            for w in h.get("initiators", []):
                init_owner[w] = h["id"]
        out: set[tuple[str, str]] = set()
        for z in self.zones.values():
            hs = {init_owner[m] for m in z.get("members", []) if m in init_owner}
            arrs = {port_owner[m] for m in z.get("members", []) if m in port_owner}
            out.update((h, a) for h in hs for a in arrs)
        return out

    def array_of_volume(self, vol: dict) -> str | None:
        pool = self.pools.get(vol.get("pool_id", ""))
        return pool.get("array_id") if pool else None

    def site_of_volume(self, vol: dict) -> str | None:
        arr = self.arrays.get(self.array_of_volume(vol) or "")
        return arr.get("site_id") if arr else None

    @cached_property
    def reachable_pairs(self) -> set[tuple[str, str]]:
        """(host_id, volume_id) the host can reach: LUN mapping AND zoning both exist."""
        out = set()
        zoned = self.host_array_zoned
        for m in self.mappings:
            v = self.volumes.get(m["volume_id"])
            if not v or v.get("in_recycle_bin"):
                continue
            if (m["host_id"], self.array_of_volume(v)) in zoned:
                out.add((m["host_id"], m["volume_id"]))
        return out

    @cached_property
    def active_paths(self) -> set[tuple[str, str]]:
        """Reachable pairs that carry I/O: host online and the volume saw IOPS in 7 days."""
        out = set()
        for h, v in self.reachable_pairs:
            host = self.hosts.get(h)
            if host and host.get("status") == "online" and self.volumes[v].get("iops_7d", 0) > 0:
                out.add((h, v))
        return out

    def app_of_volume(self, vol_id: str) -> dict | None:
        v = self.volumes.get(vol_id)
        return self.apps.get(v["app_id"]) if v and v.get("app_id") else None

    def restrict(self, keep: set[str]) -> PublicState:
        """A copy that only contains entities in ``keep`` (used by the one-hop ablation)."""
        out = PublicState(time=self.time, alerts=list(self.alerts))
        for kind in self.ENTITY_KINDS:
            setattr(out, kind, {k: v for k, v in getattr(self, kind).items() if k in keep})
        out.mappings = [m for m in self.mappings if m["host_id"] in keep and m["volume_id"] in keep]
        return out


def capture_public(twin: StorageTwin) -> PublicState:
    """Build a :class:`PublicState` by calling the read-only tools (and nothing else)."""
    r = twin.read
    info = r("get_system_info")
    ps = PublicState(time=info["time"])
    ps.sites = {x["id"]: x for x in info["sites"]}
    ps.arrays = {x["id"]: x for x in info["arrays"]}
    for p in r("list_pools")["pools"]:
        ps.pools[p["id"]] = p
        for d in r("get_pool", pool_id=p["id"])["disks"]:
            ps.disks[d["id"]] = d
    ps.clusters = {x["id"]: x for x in r("list_clusters")["clusters"]}
    ps.apps = {x["id"]: x for x in r("list_apps")["apps"]}
    ps.hosts = {x["id"]: x for x in r("list_hosts")["hosts"]}
    ps.volumes = {x["id"]: x for x in r("list_volumes")["volumes"]}
    ps.snapshots = {x["id"]: x for x in r("list_snapshots")["snapshots"]}
    ps.replication_links = {x["id"]: x for x in r("list_replication_links")["replication_links"]}
    ps.consistency_groups = {x["id"]: x for x in r("list_consistency_groups")["consistency_groups"]}
    ps.backups = {x["id"]: x for x in r("list_backups")["backups"]}
    ps.zones = {x["id"]: x for x in r("list_zones")["zones"]}
    ps.mappings = r("list_mappings")["mappings"]
    ps.alerts = r("get_alerts")["alerts"]
    return ps
