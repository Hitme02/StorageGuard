"""Build the typed dependency graph from the public API only.

Nodes are entity ids with a ``kind`` attribute; edges carry a ``type``:

=================  ==========================================
edge type          direction
=================  ==========================================
in_pool            volume -> pool, data disk -> pool
spare_of           hot-spare disk -> pool
in_array           pool -> array            (extension)
has_snapshot       volume -> snapshot
clone_of           clone volume -> parent snapshot
replicates_to      source volume -> link -> target volume
member_of_cg       volume -> consistency group
mapped_to          volume -> host
zoned_with         host -> zone, zone -> array
owned_by_app       volume -> app, consistency group -> app
in_cluster         app -> cluster, host -> cluster
backs_up           backup -> source volume
stored_in          backup -> repository volume / offsite vault
=================  ==========================================
"""

from __future__ import annotations

import networkx as nx

from ..twin.public import PublicState

OFFSITE_NODE = "offsite-vault"


def _volume_kind(v: dict) -> str:
    if v.get("role") == "backup_repository":
        return "backup_repository"
    if v.get("parent_snapshot_id"):
        return "clone"
    return "volume"


def build_graph(ps: PublicState) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()

    def node(i: str, kind: str, e: dict | None = None) -> None:
        g.add_node(i, kind=kind, name=(e or {}).get("name", ""))

    def edge(u: str, v: str, t: str) -> None:
        if u in g and v in g:
            g.add_edge(u, v, key=t, type=t)

    for a in ps.arrays.values():
        node(a["id"], "array", a)
    for p in ps.pools.values():
        node(p["id"], "pool", p)
    for d in ps.disks.values():
        node(d["id"], "disk", d)
    for c in ps.clusters.values():
        node(c["id"], "cluster", c)
    for a in ps.apps.values():
        node(a["id"], "app", a)
    for h in ps.hosts.values():
        node(h["id"], "host", h)
    for v in ps.volumes.values():
        node(v["id"], _volume_kind(v), v)
    for s in ps.snapshots.values():
        node(s["id"], "snapshot", s)
    for link in ps.replication_links.values():
        node(link["id"], "replication_link")
    for cg in ps.consistency_groups.values():
        node(cg["id"], "consistency_group", cg)
    for b in ps.backups.values():
        node(b["id"], "backup")
    for z in ps.zones.values():
        node(z["id"], "zone", z)
    node(OFFSITE_NODE, "offsite_vault")

    for p in ps.pools.values():
        edge(p["id"], p.get("array_id", ""), "in_array")
    for d in ps.disks.values():
        if d.get("pool_id"):
            edge(d["id"], d["pool_id"], "spare_of" if d.get("role") == "hot_spare" else "in_pool")
    for v in ps.volumes.values():
        edge(v["id"], v.get("pool_id", ""), "in_pool")
        if v.get("app_id"):
            edge(v["id"], v["app_id"], "owned_by_app")
        if v.get("parent_snapshot_id"):
            edge(v["id"], v["parent_snapshot_id"], "clone_of")
    for s in ps.snapshots.values():
        edge(s["volume_id"], s["id"], "has_snapshot")
    for link in ps.replication_links.values():
        edge(link["source_volume_id"], link["id"], "replicates_to")
        edge(link["id"], link["target_volume_id"], "replicates_to")
    for cg in ps.consistency_groups.values():
        for vid in cg.get("volume_ids", []):
            edge(vid, cg["id"], "member_of_cg")
        edge(cg["id"], cg.get("app_id", ""), "owned_by_app")
    for m in ps.mappings:
        edge(m["volume_id"], m["host_id"], "mapped_to")
    for a in ps.apps.values():
        edge(a["id"], a.get("cluster_id", ""), "in_cluster")
    for h in ps.hosts.values():
        edge(h["id"], h.get("cluster_id", ""), "in_cluster")
    for b in ps.backups.values():
        edge(b["id"], b["source_volume_id"], "backs_up")
        edge(b["id"], b["repository"], "stored_in")
    port_owner = {p: a["id"] for a in ps.arrays.values() for p in a.get("target_ports", [])}
    init_owner = {w: h["id"] for h in ps.hosts.values() for w in h.get("initiators", [])}
    for z in ps.zones.values():
        for m in z.get("members", []):
            if m in init_owner:
                edge(init_owner[m], z["id"], "zoned_with")
            if m in port_owner:
                edge(z["id"], port_owner[m], "zoned_with")
    return g
