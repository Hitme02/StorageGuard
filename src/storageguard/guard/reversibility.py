"""Reversibility classes R0-R3 for a (dry-run) action.

R0  trivially undoable (create, map, snapshot)
R1  undoable with disruption (unmap, pause replication, recycle-bin delete)
R2  undoable only through an existing copy (delete while another copy survives)
R3  irreversible (the last copy, or the newest recovery point, is gone)
"""

from __future__ import annotations

from typing import Any

from .analyzers import Action, Finding

R1_TOOLS = frozenset(
    {"unmap_volume", "remove_zone_member", "delete_zone", "pause_replication", "remove_hot_spare", "migrate_volume"}
)
COPY_BOUND_TOOLS = frozenset(
    {"delete_volume", "purge_recycle_bin", "delete_snapshot", "delete_backup", "restore_snapshot", "break_replication"}
)
IRREVERSIBLE_CODES = frozenset({"last_clean_copy_removed", "recovery_point_regressed", "repository_backups_lost",
                                "newest_pit_copy_removed"})  # fmt: skip


def classify(action: Action, findings: list[Finding], result: dict[str, Any]) -> int:
    """Return the reversibility class (0-3) of one executed dry-run step."""
    tool, args = action.tool, action.args
    if tool == "delete_volume" and args.get("to_recycle_bin"):
        return 1
    if tool == "set_snapshot_immutable":
        return 0 if args.get("immutable") else 1
    if tool == "resize_volume":
        return 3 if result.get("new_size_gb", 0) < result.get("old_size_gb", 0) else 0
    if tool in R1_TOOLS:
        return 1
    if tool in COPY_BOUND_TOOLS:
        return 3 if any(f.code in IRREVERSIBLE_CODES for f in findings) else 2
    return 0
