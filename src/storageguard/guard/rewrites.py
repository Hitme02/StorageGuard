"""Safe-rewrite catalogue: a safer action sequence with (most of) the same intent.

Each rewrite records whether the original intent survives.  ``partial`` means
the main effect happens but a side goal does not (a recycle-bin move does not
free capacity until the bin is purged).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..twin.public import PublicState
from .analyzers import Action


@dataclass(frozen=True)
class Rewrite:
    name: str
    steps: list[Action]
    intent_preserved: str  # "yes" | "partial"
    note: str
    escalate: bool = False  # run the preparatory steps, but still ask a human for the risky one
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"rewrite": self.name, "steps": [s.to_dict() for s in self.steps],
                "intent_preserved": self.intent_preserved, "note": self.note}  # fmt: skip


def _zone_rewrite(action: Action, ps: PublicState) -> Rewrite | None:
    """Drop target ports whose array holds volumes of another cluster mapped to the zoned host."""
    port_owner = {p: a["id"] for a in ps.arrays.values() for p in a.get("target_ports", [])}
    init_owner = {w: h["id"] for h in ps.hosts.values() for w in h.get("initiators", [])}
    if action.tool == "create_zone":
        members = list(action.args.get("members") or [])
        base_name, fabric = action.args.get("name", "zone"), action.args.get("fabric", "A")
    else:
        zone = ps.zones.get(action.args.get("zone_id", ""))
        if zone is None:
            return None
        members = [*zone.get("members", []), action.args.get("wwpn", "")]
        base_name, fabric = f"{zone['name']}_si", zone["fabric"]
    hosts = {init_owner[m] for m in members if m in init_owner}
    new_inits = [m for m in members if m in init_owner] if action.tool == "create_zone" else [action.args.get("wwpn", "")]
    if len(hosts) != 1 or not all(m in init_owner for m in new_inits):
        return None
    host = ps.hosts[next(iter(hosts))]

    def foreign(array_id: str) -> bool:
        for m in ps.mappings_by_host.get(host["id"], []):
            vol = ps.volumes.get(m["volume_id"])
            if vol is None or ps.array_of_volume(vol) != array_id:
                continue
            app = ps.app_of_volume(vol["id"])
            if app is not None and app.get("cluster_id") != host.get("cluster_id"):
                return True
        return False

    targets = [m for m in members if m in port_owner]
    safe_targets = [t for t in targets if not foreign(port_owner[t])]
    if not safe_targets or len(safe_targets) == len(targets):
        return None
    safe = [*new_inits, *safe_targets]
    step = Action("create_zone", {"name": base_name, "fabric": fabric, "members": safe})
    return Rewrite(
        "single_initiator_zone", [step], "partial",
        f"zone limited to {host['name']} and the target ports that do not expose other clusters' volumes; "
        "paths to the dropped array were not created",
    )  # fmt: skip


def candidates(action: Action, ps: PublicState) -> list[Rewrite]:
    """Catalogue lookup for one proposed action against the current public state."""
    tool, args = action.tool, action.args
    if tool == "delete_volume" and not args.get("to_recycle_bin"):
        vid = args.get("volume_id", "")
        vol = ps.volumes.get(vid)
        if vol is None or vol.get("in_recycle_bin"):
            return []
        steps = [
            Action("create_snapshot", {"volume_id": vid}),
            Action("delete_volume", {"volume_id": vid, "force": bool(args.get("force", False)), "to_recycle_bin": True}),
        ]
        return [Rewrite("snapshot_then_recycle_bin", steps, "partial",
                        "volume was snapshotted and moved to the recycle bin (kept 7 days); its capacity is "
                        "not freed until purge_recycle_bin, and it can be brought back with restore_from_recycle_bin")]  # fmt: skip
    if tool == "restore_snapshot":
        vid, sid = args.get("volume_id", ""), args.get("snapshot_id", "")
        snap, cg = ps.snapshots.get(sid), ps.cg_by_volume.get(vid)
        if snap is None or vid not in ps.volumes:
            return []
        if cg is not None and snap.get("consistency_group_id") == cg["id"]:
            steps = [
                Action("create_snapshot", {"consistency_group_id": cg["id"]}),
                Action("restore_snapshot", {"volume_id": vid, "snapshot_id": sid, "whole_consistency_group": True}),
            ]
            return [Rewrite("pre_snapshot_then_group_restore", steps, "yes",
                            f"a pre-restore group snapshot was taken and all members of {cg['name']} were restored "
                            "to the same point in time")]  # fmt: skip
        steps = [Action("create_snapshot", {"volume_id": vid}), Action("restore_snapshot", dict(args))]
        return [Rewrite("pre_snapshot_then_restore", steps, "yes",
                        "a pre-restore snapshot of the current contents was taken before restoring")]  # fmt: skip
    if tool == "break_replication":
        return [Rewrite("pause_instead_of_break", [Action("pause_replication", {"link_id": args.get("link_id", "")})],
                        "partial", "replication was paused instead of broken; the target stays a replica and "
                        "resume_replication restores protection without a full resync")]  # fmt: skip
    if tool == "resize_volume":
        vol = ps.volumes.get(args.get("volume_id", ""))
        if vol is not None and isinstance(args.get("new_size_gb"), int) and args["new_size_gb"] < vol["provisioned_gb"]:
            steps = [Action("create_snapshot", {"volume_id": vol["id"]}), Action("resize_volume", dict(args))]
            return [Rewrite("snapshot_then_shrink", steps, "yes",
                            "a snapshot is taken before the shrink; the shrink itself needs human approval", escalate=True)]  # fmt: skip
    if tool in ("create_zone", "add_zone_member"):
        rw = _zone_rewrite(action, ps)
        return [rw] if rw else []
    return []
