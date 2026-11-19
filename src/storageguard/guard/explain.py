"""Structured feedback for the agent: reasons, impacted entities with edge paths, alternatives."""

from __future__ import annotations

from typing import Any

from ..twin.public import PublicState
from .analyzers import Action, _cleanish, lineage_holders, lineage_root
from .config import GuardConfig
from .policy import BLOCK, Decision

MAX_IMPACTED = 8


def _impacted(decision: Decision) -> list[dict[str, Any]]:
    imp = [i for i in decision.assessment.impacted if i.hops > 0]
    imp.sort(key=lambda i: (i.kind != "app", i.mode != "destroyed", i.hops))
    return [i.to_dict() for i in imp[:MAX_IMPACTED]]


def suggest_alternatives(decision: Decision, cfg: GuardConfig) -> list[str]:
    """Concrete, state-aware next steps for the agent (derived from public state only)."""
    ps: PublicState | None = decision.before
    action: Action = decision.action
    if ps is None:
        return []
    codes = {f.code: f for f in decision.assessment.findings}
    out: list[str] = []
    args = action.args

    if action.tool == "delete_snapshot" and codes.keys() & {
        "last_clean_copy_removed", "recovery_point_regressed", "last_clean_snapshot_removed"
    }:  # fmt: skip
        snap = ps.snapshots.get(args.get("snapshot_id", ""))
        if snap:
            root = lineage_root(ps, snap["volume_id"])
            holders = lineage_holders(ps, root)
            enc = [h.time for h in holders.values() if h.status == "encrypted"]
            suspect, t_enc = bool(enc), min(enc) if enc else 0
            siblings = [s for s in ps.snapshots_by_volume.get(snap["volume_id"], []) if s["id"] != snap["id"]]
            clean = [s for s in [*siblings, snap] if _cleanish(holders[s["id"]], suspect, t_enc)]
            newest = max(clean, key=lambda s: s["created_at"])["id"] if clean else None
            safe = [s["id"] for s in siblings if s["id"] != newest and not s["immutable"]
                    and not ps.clones_by_snapshot.get(s["id"])
                    and (not _cleanish(holders[s["id"]], suspect, t_enc) or len(clean) > 1)]  # fmt: skip
            keep = f" Keep {newest}: it is the newest clean recovery point." if newest else ""
            if safe:
                out.append(f"Delete other snapshots of this volume instead, e.g. {', '.join(safe[:5])}.{keep}")
            else:
                out.append(f"No other snapshot of this volume can be deleted safely.{keep}")
    if "repository_backups_lost" in codes:
        vid = codes["repository_backups_lost"].entity
        srcs = sorted({b["source_volume_id"] for b in ps.backups_by_repository.get(vid, [])})
        out.append(
            "First create another copy of those backups, e.g. run_backup(volume_id=<source>, repository=\"offsite-vault\") "
            f"for {', '.join(srcs[:4])}; then retry."
        )
    if "replication_broken" in codes and action.tool == "break_replication":
        out.append(f"Use pause_replication(link_id=\"{args.get('link_id')}\") instead; it can be resumed without a full resync.")
    elif "replication_broken" in codes or "replication_paused" in codes:
        out.append("This volume is part of an active replication pair that protects another volume; leave it in place "
                   "or get explicit approval via ask_human.")  # fmt: skip
    if "degraded_pool_without_spare" in codes:
        pid = codes["degraded_pool_without_spare"].entity
        arr = ps.pools.get(pid, {}).get("array_id")
        donors = [p for p in ps.pools.values() if p["id"] != pid and p.get("array_id") == arr
                  and p.get("state") == "healthy" and len(p.get("hot_spare_ids", [])) >= 2]  # fmt: skip
        if donors:
            out.append("Take the spare from a healthy pool with more than one hot spare instead: "
                       + ", ".join(f"{p['id']} ({p['name']}, {len(p['hot_spare_ids'])} spares)" for p in donors) + ".")  # fmt: skip
        else:
            out.append("No healthy pool on this array has a hot spare to give; wait for the rebuild or ask_human.")
    if codes.keys() & {"pool_utilisation_high", "overcommit_exceeded"}:
        f = codes.get("pool_utilisation_high") or codes["overcommit_exceeded"]
        arr = ps.pools.get(f.entity, {}).get("array_id")
        roomy = sorted((p for p in ps.pools.values() if p["id"] != f.entity and p.get("array_id") == arr),
                       key=lambda p: -p.get("free_gb", 0))  # fmt: skip
        if roomy:
            names = ", ".join(f"{p['id']} ({p['name']}, {p['free_gb']} GB free, {p['utilisation_pct']}% used)" for p in roomy[:3])
            verb = "create the volume in" if action.tool == "create_volume" else "migrate_volume the volume to"
            out.append(f"Pool {f.entity} is too full. Consider another pool on the same array: {verb} {names}.")
    if codes.keys() & {"cross_tenant_access", "cross_cluster_access"}:
        f = codes.get("cross_tenant_access") or codes["cross_cluster_access"]
        if action.tool == "map_volume":
            app = ps.app_of_volume(f.entity)
            if app:
                hosts = [h["id"] for h in ps.hosts.values() if h.get("cluster_id") == app.get("cluster_id")]
                out.append(f"Map {f.entity} only to hosts of its own cluster: {', '.join(hosts[:6])}.")
        else:
            out.append(f"A host in this zone still has a LUN mapping to {f.entity}, which belongs to another cluster. "
                       "Remove that stale mapping with unmap_volume first (after confirming it is stale), then zone the host.")  # fmt: skip
    if "immutability_removed" in codes:
        out.append("Immutable copies may only be released by a human storage/security administrator; "
                   "free space another way or use ask_human.")  # fmt: skip
    if "active_path_removed" in codes:
        out.append("An online host is still doing I/O to this volume. Only remove paths of hosts that are offline/retired, "
                   "or confirm with ask_human.")  # fmt: skip
    if "cg_split" in codes:
        out.append("Restore the whole group: restore_snapshot(..., whole_consistency_group=true).")
    if action.tool == "restore_snapshot" and codes.keys() & {"recovery_point_regressed", "last_clean_copy_removed"}:
        out.append("Take create_snapshot of the volume (or its consistency group) first, then restore.")
    if codes.keys() & {"last_clean_copy_removed", "only_copy_in_recycle_bin"} and action.tool in ("delete_volume", "purge_recycle_bin"):
        out.append("If the data must really go, make sure another copy exists first (run_backup to \"offsite-vault\"), "
                   "or use delete_volume(..., to_recycle_bin=true), or get approval via ask_human.")  # fmt: skip
    if "last_offsite_copy_removed" in codes and not out:
        out.append("Create another offsite copy first (run_backup to \"offsite-vault\"), or leave this copy in place.")
    if not out and decision.kind == BLOCK:
        out.append("Use ask_human to confirm the intent, or choose a less destructive way to meet the goal.")
    return out


def build_feedback(decision: Decision, status: str, cfg: GuardConfig, result: Any = None, note: str | None = None) -> dict[str, Any]:
    """The JSON the agent receives for a guarded (non-ALLOW) action."""
    a = decision.assessment
    if not cfg.structured_feedback:  # ablation A4
        if status in ("blocked", "denied_by_human", "pending_approval"):
            return {"status": "denied"}
        out: dict[str, Any] = {"status": status}
        if decision.rewrite is not None and status == "rewritten":
            out["rewritten_to"] = [s.to_dict() for s in decision.execute]
        if result is not None:
            out["result"] = result
        return out
    reasons = [f.detail for f in sorted(a.findings, key=lambda f: -f.severity)[:5]]
    fb: dict[str, Any] = {
        "status": status,
        "decision": decision.kind,
        "risk": a.risk,
        "reasons": reasons,
        "impacted": _impacted(decision),
        "invariants_violated": [v.to_dict() for v in a.violations],
    }
    if decision.rewrite is not None and status in ("rewritten", "executed"):
        fb["rewritten_to"] = [s.to_dict() for s in decision.execute]
        fb["rewrite_note"] = decision.rewrite.note
        fb["intent_preserved"] = decision.rewrite.intent_preserved
    if status in ("blocked", "denied_by_human", "pending_approval"):
        fb["suggested_alternatives"] = suggest_alternatives(decision, cfg)
    if note:
        fb["note"] = note
    if result is not None:
        fb["result"] = result
    return fb
