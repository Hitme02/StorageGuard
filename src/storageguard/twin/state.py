"""``TwinState`` (pure data) and ``StorageTwin`` (state + clock + operation log).

The twin is deterministic: ids come from counters stored in the state, time is
a simulated clock that advances one minute per successful mutation, and there
is no randomness anywhere in the operations.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from .models import (
    COLLECTIONS,
    ID_PREFIX,
    Alert,
    App,
    Array,
    BackupCopy,
    Cluster,
    ConsistencyGroup,
    Disk,
    Entity,
    Host,
    Mapping,
    Pool,
    ReplicationLink,
    Site,
    Snapshot,
    Volume,
    Zone,
)
from .oplog import OpLog, OpLogEntry, compute_diff

RECYCLE_TTL_MINUTES = 7 * 24 * 60


class OpError(Exception):
    """A storage operation was rejected (precondition failed). State is unchanged."""


class TwinState(BaseModel):
    """All entities of the simulated environment plus the clock and id counters."""

    sites: dict[str, Site] = {}
    arrays: dict[str, Array] = {}
    pools: dict[str, Pool] = {}
    disks: dict[str, Disk] = {}
    clusters: dict[str, Cluster] = {}
    apps: dict[str, App] = {}
    hosts: dict[str, Host] = {}
    volumes: dict[str, Volume] = {}
    snapshots: dict[str, Snapshot] = {}
    replication_links: dict[str, ReplicationLink] = {}
    consistency_groups: dict[str, ConsistencyGroup] = {}
    backups: dict[str, BackupCopy] = {}
    mappings: dict[str, Mapping] = {}
    zones: dict[str, Zone] = {}
    alerts: dict[str, Alert] = {}
    now: int = 0
    counters: dict[str, int] = {}

    def fast_copy(self) -> TwinState:
        """Deep-enough copy relying on the copy-on-write discipline for list fields."""
        data: dict[str, Any] = {
            kind: {k: v.model_copy() for k, v in getattr(self, kind).items()} for kind in COLLECTIONS
        }
        data["now"] = self.now
        data["counters"] = dict(self.counters)
        return TwinState.model_construct(**data)

    # ---------------- capacity accounting ----------------
    @staticmethod
    def volume_consumed_gb(v: Volume) -> int:
        """Thin volumes consume what is written; thick volumes consume their full size."""
        return v.used_gb if v.thin else v.provisioned_gb

    def pool_snapshot_gb(self, pool_id: str) -> int:
        vols = self.volumes
        return sum(s.size_gb for s in self.snapshots.values() if vols[s.volume_id].pool_id == pool_id)

    def pool_used_gb(self, pool_id: str) -> int:
        """Physical consumption: volumes (including the recycle bin) plus snapshots."""
        used = sum(self.volume_consumed_gb(v) for v in self.volumes.values() if v.pool_id == pool_id)
        return used + self.pool_snapshot_gb(pool_id)

    def pool_provisioned_gb(self, pool_id: str) -> int:
        return sum(
            v.provisioned_gb for v in self.volumes.values() if v.pool_id == pool_id and not v.in_recycle_bin
        )

    def pool_utilisation(self, pool_id: str) -> float:
        return self.pool_used_gb(pool_id) / self.pools[pool_id].physical_gb

    def pool_overcommit(self, pool_id: str) -> float:
        return self.pool_provisioned_gb(pool_id) / self.pools[pool_id].physical_gb

    # ---------------- topology helpers ----------------
    def array_of_volume(self, volume_id: str) -> str:
        return self.pools[self.volumes[volume_id].pool_id].array_id

    def site_of_volume(self, volume_id: str) -> str:
        return self.arrays[self.array_of_volume(volume_id)].site_id

    def host_zoned_to_array(self, host_id: str, array_id: str) -> bool:
        inits = set(self.hosts[host_id].initiators)
        ports = set(self.arrays[array_id].target_ports)
        return any(inits & set(z.members) and ports & set(z.members) for z in self.zones.values())

    def reachable(self, host_id: str, volume_id: str) -> bool:
        """A host reaches a volume iff a LUN mapping exists AND it is zoned to the array."""
        v = self.volumes.get(volume_id)
        if v is None or v.in_recycle_bin or f"{host_id}|{volume_id}" not in self.mappings:
            return False
        return self.host_zoned_to_array(host_id, self.array_of_volume(volume_id))

    def content_hash(self) -> str:
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


class StorageTwin:
    """The simulated storage environment that tools operate on."""

    def __init__(self, state: TwinState | None = None, oplog: OpLog | None = None) -> None:
        self.state = state or TwinState()
        self.oplog = oplog or OpLog()
        self._pre: dict[tuple[str, str], Entity | None] | None = None

    # ---------------- ids & clock ----------------
    def new_id(self, kind: str) -> str:
        n = self.state.counters.get(kind, 0) + 1
        self.state.counters[kind] = n
        return f"{ID_PREFIX[kind]}-{n}"

    @property
    def now(self) -> int:
        return self.state.now

    # ---------------- tracked mutation primitives ----------------
    def _track(self, kind: str, key: str) -> None:
        if self._pre is not None and (kind, key) not in self._pre:
            cur = getattr(self.state, kind).get(key)
            self._pre[(kind, key)] = cur.model_copy() if cur is not None else None

    def mut(self, kind: str, key: str) -> Any:
        """Fetch an entity for modification (records its pre-image for the log)."""
        self._track(kind, key)
        return getattr(self.state, kind)[key]

    def add(self, kind: str, entity: Any, key: str | None = None) -> Any:
        key = key or entity.id
        self._track(kind, key)
        getattr(self.state, kind)[key] = entity
        return entity

    def remove(self, kind: str, key: str) -> None:
        self._track(kind, key)
        del getattr(self.state, kind)[key]

    # ---------------- execution ----------------
    def execute(self, op: str, args: dict[str, Any] | None = None, actor: str = "agent") -> dict[str, Any]:
        """Run a tool by name. Never raises for domain errors: returns ``{"ok": False, "error"}``.

        Mutations are atomic (rolled back on error) and appended to the operation log.
        """
        from .operations import OPS, validate_args  # local import: operations imports this module

        spec = OPS.get(op)
        if spec is None:
            return {"ok": False, "error": f"unknown tool '{op}'", "malformed": True}
        try:
            kwargs = validate_args(spec, args or {})
        except ValueError as exc:
            return {"ok": False, "error": f"invalid arguments for {op}: {exc}", "malformed": True}
        if not spec.mutating:
            try:
                return {"ok": True, **spec.fn(self, **kwargs)}
            except OpError as exc:
                return {"ok": False, "error": str(exc)}

        self._pre = {}
        now0, counters0 = self.state.now, dict(self.state.counters)
        try:
            result = spec.fn(self, **kwargs)
            ok, error = True, None
        except OpError as exc:
            for (kind, key), before in self._pre.items():  # atomic rollback
                coll = getattr(self.state, kind)
                if before is None:
                    coll.pop(key, None)
                else:
                    coll[key] = before
            self.state.counters = counters0
            self.state.now = now0
            result, ok, error = {}, False, str(exc)
            self._pre = {}
        diff = compute_diff(self._pre, self.state)
        self._pre = None
        self.oplog.append(
            OpLogEntry(seq=len(self.oplog), time=now0, op=op, args=dict(args or {}), actor=actor, ok=ok, error=error, diff=diff)
        )
        if ok:
            self.state.now += 1  # every successful mutation takes one simulated minute
            return {"ok": True, **result}
        return {"ok": False, "error": error}

    def read(self, op: str, **kwargs: Any) -> dict[str, Any]:
        """Call a read-only tool directly (used by ``capture_public``)."""
        from .operations import OPS

        spec = OPS[op]
        assert not spec.mutating, op
        return spec.fn(self, **kwargs)

    def resolve_args(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        """Replace unique entity *names* by ids in ``*_id`` arguments (names are public)."""
        from .operations import ARG_COLLECTION

        out = dict(args)
        for arg, val in args.items():
            kind = ARG_COLLECTION.get(arg)
            if kind is None or not isinstance(val, str):
                continue
            coll = getattr(self.state, kind)
            if val in coll:
                continue
            hits = [e.id for e in coll.values() if getattr(e, "name", None) == val]
            if len(hits) == 1:
                out[arg] = hits[0]
        return out

    # ---------------- copy / persistence ----------------
    def clone(self, with_log: bool = False) -> StorageTwin:
        """Independent copy for dry runs (the log is omitted unless requested)."""
        log = OpLog(entries=list(self.oplog.entries)) if with_log else OpLog()
        return StorageTwin(self.state.fast_copy(), log)

    def to_json(self) -> str:
        return json.dumps(
            {"state": self.state.model_dump(mode="json"), "oplog": self.oplog.model_dump(mode="json")},
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> StorageTwin:
        raw = json.loads(text)
        return cls(TwinState.model_validate(raw["state"]), OpLog.model_validate(raw.get("oplog", {})))

    @classmethod
    def replay(cls, initial: TwinState, oplog: OpLog) -> StorageTwin:
        """Re-execute a log on a copy of ``initial``; reproduces the logged state exactly."""
        twin = cls(TwinState.model_validate(initial.model_dump()))
        for e in oplog.entries:
            twin.execute(e.op, e.args, actor=e.actor)
        return twin
