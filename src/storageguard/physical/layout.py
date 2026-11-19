"""Where each object lives in the sandbox, derived from public fields only.

    sites/<site>/<array>/<pool>/<volume>/data.img              the volume's current image
    sites/<site>/<array>/<pool>/<volume>/snapshots/<snap>.img  its snapshots (they live in the volume)
    sites/<site>/<array>/<pool>/<volume>/backups/<bkp>.img     backups stored in a repository volume
    sites/<site>/<array>/recycle-bin/<volume>/...              a volume in the recycle bin
    vault/<bkp>.img                                            the offsite backup vault

Keeping snapshots and stored backups *inside* their volume's directory makes the
cascades structural: removing a volume's directory removes them, as on an array.
"""

from __future__ import annotations

from typing import Any

from ..twin.models import OFFSITE_VAULT
from ..twin.public import to_public

VAULT = "vault"


def volume_dir(state: Any, volume_id: str) -> str:
    v = to_public("volumes", state.volumes[volume_id])
    pool = to_public("pools", state.pools[v["pool_id"]])
    arr = to_public("arrays", state.arrays[pool["array_id"]])
    if v["in_recycle_bin"]:
        return f"sites/{arr['site_id']}/{arr['id']}/recycle-bin/{volume_id}"
    return f"sites/{arr['site_id']}/{arr['id']}/{pool['id']}/{volume_id}"


def image(vdir: str) -> str:
    return f"{vdir}/data.img"


def snapshot_file(vdir: str, snapshot_id: str) -> str:
    return f"{vdir}/snapshots/{snapshot_id}.img"


def backup_file(repository: str, backup_id: str, loc: dict[str, str]) -> str:
    if repository == OFFSITE_VAULT:
        return f"{VAULT}/{backup_id}.img"
    return f"{loc[repository]}/backups/{backup_id}.img"


def site_dir(site_id: str) -> str:
    return f"sites/{site_id}"
