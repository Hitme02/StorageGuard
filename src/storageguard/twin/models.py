"""Internal entity models of the storage twin.

These models hold the *full* truth about the simulated array, including fields
that no agent, guard or baseline may ever see (``INTERNAL_FIELDS``).  Everything
that crosses the observability boundary goes through :mod:`storageguard.twin.public`.

All times are integer minutes on a simulated clock (see ``public.iso``); nothing
in the twin reads the wall clock.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Entity(BaseModel):
    """Base class: strict fields, cheap shallow copies.

    Operations follow a copy-on-write discipline for list fields (they assign a
    new list instead of mutating in place), which makes ``model_copy()`` a safe
    and fast way to snapshot an entity for dry runs and the operation log.
    """

    model_config = ConfigDict(extra="forbid")


class Site(Entity):
    """A data centre. The DR site holds replica targets for disaster recovery."""

    id: str
    name: str
    role: Literal["primary", "dr"]


class Array(Entity):
    """A storage array. ``target_ports`` are the WWPNs hosts are zoned to."""

    id: str
    site_id: str
    name: str
    target_ports: list[str]


class Pool(Entity):
    """A RAID-protected pool of disks from which volumes are carved.

    ``overcommit_limit`` caps provisioned/physical for thin provisioning.
    ``state`` is degraded when a disk failed with no spare, rebuilding while a
    hot spare is resynchronising.
    """

    id: str
    array_id: str
    name: str
    raid_level: Literal["RAID5", "RAID6", "RAID10"]
    physical_gb: int
    disk_ids: list[str]
    hot_spare_ids: list[str]
    state: Literal["healthy", "degraded", "rebuilding"] = "healthy"
    overcommit_limit: float = 2.0


class Disk(Entity):
    """A physical drive: a data member, an idle hot spare, or unassigned."""

    id: str
    pool_id: str | None
    role: Literal["data", "hot_spare", "unassigned"]
    state: Literal["ok", "failed", "rebuilding"] = "ok"
    size_gb: int = 1920


class Cluster(Entity):
    """A host cluster owned by one tenant: the isolation boundary for access."""

    id: str
    name: str
    tenant: str


class App(Entity):
    """A business application. Tier 1 is mission critical, tier 3 is best effort."""

    id: str
    name: str
    tier: Literal[1, 2, 3]
    cluster_id: str


class Host(Entity):
    """A server with FC initiators (HBA WWPNs). ``status`` is an extension to the
    spec: an offline (retired / powered-off) host has no *active* paths."""

    id: str
    name: str
    cluster_id: str
    initiators: list[str]
    status: Literal["online", "offline"] = "online"


VolumeRole = Literal["primary", "replica_target", "clone", "backup_repository", "scratch"]


class Volume(Entity):
    """A block volume (LUN).

    Internal-only fields describe the *content*: ``data_id`` names the data
    lineage, ``content_time`` the point in time it is current to, and
    ``true_clean`` whether it is free of ransomware encryption.  A clone also
    remembers the lineage it was copied from (``origin_*``).
    """

    id: str
    name: str
    pool_id: str
    app_id: str | None = None
    provisioned_gb: int
    used_gb: int = 0
    thin: bool = True
    role: VolumeRole = "primary"
    description: str = ""
    tags: list[str] = []
    created_at: int = 0
    last_io_at: int | None = None
    iops_7d: int = 0
    in_recycle_bin: bool = False
    purge_at: int | None = None
    parent_snapshot_id: str | None = None
    qos_iops_limit: int | None = None
    qos_mbps_limit: int | None = None
    # ---- internal only ----
    data_id: str = ""
    content_time: int = 0
    true_clean: bool = True
    origin_data_id: str | None = None
    origin_time: int | None = None
    origin_clean: bool | None = None


ScanStatus = Literal["clean", "encrypted", "unscanned"]


class Snapshot(Entity):
    """A point-in-time, space-efficient image of a volume."""

    id: str
    volume_id: str
    name: str = ""
    created_at: int
    size_gb: int
    immutable: bool = False
    scan_status: ScanStatus = "unscanned"
    consistency_group_id: str | None = None
    # ---- internal only ----
    data_id: str = ""
    point_in_time: int = 0
    true_clean: bool = True


class ReplicationLink(Entity):
    """Remote replication of a source volume to a target volume on another array."""

    id: str
    source_volume_id: str
    target_volume_id: str
    mode: Literal["sync", "async"]
    rpo_minutes: int
    state: Literal["active", "paused", "broken"] = "active"


class ConsistencyGroup(Entity):
    """Volumes of one application that must be snapshotted/restored together."""

    id: str
    name: str
    app_id: str
    volume_ids: list[str]


class BackupCopy(Entity):
    """A backup image of a volume, stored in a repository volume or offsite vault."""

    id: str
    source_volume_id: str
    repository: str  # a volume id, or "offsite-vault"
    created_at: int
    size_gb: int
    immutable: bool = False
    scan_status: ScanStatus = "unscanned"
    # ---- internal only ----
    data_id: str = ""
    point_in_time: int = 0
    true_clean: bool = True


class Mapping(Entity):
    """LUN masking entry: the array presents ``volume_id`` to ``host_id``."""

    host_id: str
    volume_id: str
    lun_id: int

    @property
    def key(self) -> str:
        return mapping_key(self.host_id, self.volume_id)


def mapping_key(host_id: str, volume_id: str) -> str:
    return f"{host_id}|{volume_id}"


class Zone(Entity):
    """An FC zone: only member WWPNs in the same zone can talk to each other."""

    id: str
    fabric: Literal["A", "B"]
    name: str
    members: list[str]


class Alert(Entity):
    """An array event. The free-text message is untrusted (may carry injections)."""

    id: str
    time: int
    severity: Literal["info", "warning", "critical"]
    message: str


OFFSITE_VAULT = "offsite-vault"

#: collection name -> model
COLLECTIONS: dict[str, type[Entity]] = {
    "sites": Site,
    "arrays": Array,
    "pools": Pool,
    "disks": Disk,
    "clusters": Cluster,
    "apps": App,
    "hosts": Host,
    "volumes": Volume,
    "snapshots": Snapshot,
    "replication_links": ReplicationLink,
    "consistency_groups": ConsistencyGroup,
    "backups": BackupCopy,
    "mappings": Mapping,
    "zones": Zone,
    "alerts": Alert,
}

#: fields that must never cross the observability boundary
INTERNAL_FIELDS: dict[str, frozenset[str]] = {
    "volumes": frozenset(
        {"data_id", "content_time", "true_clean", "origin_data_id", "origin_time", "origin_clean"}
    ),
    "snapshots": frozenset({"data_id", "point_in_time", "true_clean"}),
    "backups": frozenset({"data_id", "point_in_time", "true_clean"}),
}

ID_PREFIX: dict[str, str] = {
    "sites": "site",
    "arrays": "arr",
    "pools": "pool",
    "disks": "disk",
    "clusters": "cl",
    "apps": "app",
    "hosts": "host",
    "volumes": "vol",
    "snapshots": "snap",
    "replication_links": "rl",
    "consistency_groups": "cg",
    "backups": "bkp",
    "zones": "zone",
    "alerts": "alert",
}
