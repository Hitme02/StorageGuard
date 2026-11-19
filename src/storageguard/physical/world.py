"""Write a scenario's initial data to the sandbox: the real-world bytes the agent will act on.

This is the only module of the physical layer that reads the twin's hidden fields
(``data_id``, ``content_time``, ``point_in_time``, ``true_clean``, ``origin_*``): they say
what data each object holds, which is exactly what the initial files must contain.
After this, the driver and the measurement work on public fields and file contents only.

A clone, and every snapshot or backup taken of it, physically contains the parent's
data it was cloned from (the oracle counts that only for the clone volume itself).
Ransomware-encrypted objects (``true_clean`` false) are written encrypted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..twin.models import OFFSITE_VAULT
from ..twin.state import TwinState
from .image import COMMIT_STEP_MIN, encrypt_lines, render, segment
from .layout import backup_file, image, snapshot_file, volume_dir
from .sandbox import Sandbox


@dataclass(frozen=True)
class LineageInfo:
    """One dataset that exists at the start, and who owns it (for reporting)."""

    lineage: str
    owner: str  # owning (non-replica) volume at the start
    gb: int
    tier: int | None
    app: str | None


@dataclass
class Manifest:
    lineages: dict[str, LineageInfo] = field(default_factory=dict)  # every dataset with a clean copy at the start
    volume_lineage: dict[str, str] = field(default_factory=dict)  # start volume -> the dataset it holds
    loc: dict[str, str] = field(default_factory=dict)  # start volume -> its directory
    files: int = 0
    bytes: int = 0


def materialise(state: TwinState, sb: Sandbox) -> Manifest:
    times = [v.content_time for v in state.volumes.values()]
    times += [s.point_in_time for s in state.snapshots.values()] + [b.point_in_time for b in state.backups.values()]
    times += [v.origin_time for v in state.volumes.values() if v.origin_time is not None]
    base = (min(times, default=state.now) // COMMIT_STEP_MIN - 1) * COMMIT_STEP_MIN
    start = {v.data_id: v.created_at for v in state.volumes.values() if v.origin_data_id}  # a clone's own writes

    def seg(lineage: str, point: int, clean: bool, key: str) -> list[str]:
        lines = segment(lineage, min(start.get(lineage, base), point), point)
        return lines if clean else [encrypt_lines(lines, key)]

    def inherited(volume_id: str, key: str) -> list[str]:
        v = state.volumes.get(volume_id)
        if v is None or not v.origin_data_id:
            return []
        return seg(v.origin_data_id, v.origin_time or 0, bool(v.origin_clean), key + ":origin")

    m = Manifest()
    m.loc = {vid: volume_dir(state, vid) for vid in state.volumes}

    def put(rel: str, lines: list[str]) -> None:
        text = render(lines)
        sb.write(rel, text)
        m.files += 1
        m.bytes += len(text.encode())

    for v in state.volumes.values():
        put(image(m.loc[v.id]), inherited(v.id, v.id) + seg(v.data_id, v.content_time, v.true_clean, v.id))
        m.volume_lineage[v.id] = v.data_id
    for s in state.snapshots.values():
        rel = snapshot_file(m.loc[s.volume_id], s.id)
        put(rel, inherited(s.volume_id, s.id) + seg(s.data_id, s.point_in_time, s.true_clean, s.id))
        if s.immutable:
            sb.lock(rel)
    for b in state.backups.values():
        if b.repository != OFFSITE_VAULT and b.repository not in state.volumes:
            continue
        rel = backup_file(b.repository, b.id, m.loc)
        put(rel, inherited(b.source_volume_id, b.id) + seg(b.data_id, b.point_in_time, b.true_clean, b.id))
        if b.immutable:
            sb.lock(rel)

    # who owns each dataset (same rule as the oracle's reporting: the first non-replica volume holding it)
    clean: set[str] = {v.data_id for v in state.volumes.values() if v.true_clean}
    clean |= {v.origin_data_id for v in state.volumes.values() if v.origin_data_id and v.origin_clean}
    clean |= {s.data_id for s in state.snapshots.values() if s.true_clean}
    clean |= {b.data_id for b in state.backups.values() if b.true_clean}
    owners: dict[str, str] = {}
    for v in state.volumes.values():
        if v.role != "replica_target":
            owners.setdefault(v.data_id, v.id)
    for lineage in sorted(clean):
        own = owners.get(lineage, "")
        vol = state.volumes.get(own)
        app = state.apps.get(vol.app_id) if vol and vol.app_id else None
        m.lineages[lineage] = LineageInfo(lineage, own, vol.used_gb if vol else 0, app.tier if app else None,
                                          app.name if app else None)  # fmt: skip
    return m
