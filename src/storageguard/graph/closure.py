"""Blast-radius closure over the dependency graph, with traceable edge paths.

``closure`` starts from the entities an action touches and follows *impact
propagation rules*: which neighbours are destroyed with, or affected by, the
entity under change.  Every impacted entity comes with the edge path that
explains it, e.g.::

    vol-7 <-stored_in- bkp-12 -backs_up-> vol-3 (ora-prod-data-01) -owned_by_app-> app-2 (ora-erp)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import networkx as nx

# (mode, node kind) -> list of (edge type, direction, next mode)
#   direction "out": follow node -> neighbour edges;  "in": neighbour -> node edges
#   mode "destroyed": the entity goes away;  "affected": it loses protection/access/redundancy
_VOL = ("volume", "clone", "backup_repository")
_RULES: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
for _k in _VOL:
    _RULES[("destroyed", _k)] = [
        ("has_snapshot", "out", "destroyed"),
        ("stored_in", "in", "destroyed"),
        ("replicates_to", "out", "destroyed"),
        ("replicates_to", "in", "destroyed"),
        ("mapped_to", "out", "affected"),
        ("member_of_cg", "out", "affected"),
        ("owned_by_app", "out", "affected"),
    ]
    _RULES[("affected", _k)] = [("owned_by_app", "out", "affected"), ("member_of_cg", "out", "affected")]
    _RULES[("access", _k)] = [("mapped_to", "out", "affected"), ("owned_by_app", "out", "affected")]
_RULES[("destroyed", "snapshot")] = [("clone_of", "in", "destroyed"), ("has_snapshot", "in", "affected")]
_RULES[("affected", "snapshot")] = [("has_snapshot", "in", "affected")]
_RULES[("destroyed", "backup")] = [("backs_up", "out", "affected")]
_RULES[("destroyed", "replication_link")] = [("replicates_to", "in", "affected"), ("replicates_to", "out", "affected")]
_RULES[("affected", "replication_link")] = _RULES[("destroyed", "replication_link")]
_RULES[("affected", "consistency_group")] = [("owned_by_app", "out", "affected"), ("member_of_cg", "in", "affected")]
_RULES[("access", "host")] = [("mapped_to", "in", "affected")]
_RULES[("destroyed", "zone")] = [("zoned_with", "in", "access")]
_RULES[("affected", "zone")] = [("zoned_with", "in", "access")]
_RULES[("affected", "disk")] = [("spare_of", "out", "affected"), ("in_pool", "out", "affected")]
_RULES[("affected", "pool")] = [("in_pool", "in", "affected")]


@dataclass(frozen=True)
class Impact:
    entity: str
    kind: str
    mode: str  # destroyed | affected
    hops: int
    path: str

    def to_dict(self) -> dict:
        return {"id": self.entity, "kind": self.kind, "effect": self.mode, "path": self.path}


def _label(g: nx.MultiDiGraph, n: str) -> str:
    name = g.nodes[n].get("name")
    return f"{n} ({name})" if name else n


def closure(
    g: nx.MultiDiGraph, starts: list[tuple[str, str]], max_hops: int | None = None
) -> list[Impact]:
    """Impacted entities reachable from ``starts`` = [(entity id, mode), ...].

    ``max_hops=1`` gives the one-hop ablation; ``None`` is the full transitive closure.
    """
    out: dict[str, Impact] = {}
    queue: deque[tuple[str, str, int, str]] = deque()
    for ent, mode in starts:
        if ent in g:
            queue.append((ent, mode, 0, _label(g, ent)))
            out[ent] = Impact(ent, g.nodes[ent]["kind"], "affected" if mode == "access" else mode, 0, _label(g, ent))
    while queue:
        node, mode, hops, path = queue.popleft()
        if max_hops is not None and hops >= max_hops:
            continue
        kind = g.nodes[node]["kind"]
        for etype, direction, nmode in _RULES.get((mode, kind), []):
            if direction == "out":
                nbrs = [(v, f"{path} -{etype}-> ") for _, v, k in g.out_edges(node, keys=True) if k == etype]
            else:
                nbrs = [(u, f"{path} <-{etype}- ") for u, _, k in g.in_edges(node, keys=True) if k == etype]
            for nb, prefix in nbrs:
                shown = "affected" if nmode == "access" else nmode
                prev = out.get(nb)
                if prev is not None and (prev.mode == "destroyed" or prev.mode == shown):
                    continue
                npath = prefix + _label(g, nb)
                out[nb] = Impact(nb, g.nodes[nb]["kind"], shown, hops + 1, npath)
                queue.append((nb, nmode, hops + 1, npath))
    return sorted(out.values(), key=lambda i: (i.hops, i.entity))


def one_hop_neighbourhood(g: nx.MultiDiGraph, entities: list[str]) -> set[str]:
    """The entities plus their direct graph neighbours (either edge direction)."""
    keep: set[str] = set()
    for e in entities:
        if e in g:
            keep.add(e)
            keep.update(g.successors(e))
            keep.update(g.predecessors(e))
    return keep
