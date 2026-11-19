"""Phase 2: dependency graph and blast-radius closure."""

from storageguard.graph.builder import build_graph
from storageguard.graph.closure import closure, one_hop_neighbourhood
from storageguard.twin import capture_public


def graph(twin):
    return build_graph(capture_public(twin))


def by_id(impacts):
    return {i.entity: i for i in impacts}


def test_node_kinds_and_edge_types(twin, ids):
    g = graph(twin)
    assert g.nodes[ids.data]["kind"] == "volume"
    assert g.nodes[ids.clone]["kind"] == "clone"
    assert g.nodes[ids.repo]["kind"] == "backup_repository"
    types = {k for _, _, k in g.edges(keys=True)}
    assert types >= {"has_snapshot", "clone_of", "replicates_to", "member_of_cg", "mapped_to", "zoned_with",
                     "owned_by_app", "in_cluster", "backs_up", "stored_in", "in_pool", "spare_of"}  # fmt: skip


def test_graph_is_built_from_public_data_only(twin):
    g = graph(twin)
    for _, attrs in g.nodes(data=True):
        assert set(attrs) <= {"kind", "name"}


def test_closure_delete_repository_reaches_protected_app(twin, ids):
    imp = by_id(closure(graph(twin), [(ids.repo, "destroyed")]))
    assert imp[ids.crm_backups[0]].mode == "destroyed"
    assert imp[ids.crm].mode == "affected" and imp[ids.app_crm].mode == "affected"
    path = imp[ids.app_crm].path
    assert path.startswith(f"{ids.repo} (bkp-repo-01) <-stored_in- ")
    assert "-backs_up-> " in path and path.endswith("-owned_by_app-> " + f"{ids.app_crm} (sql-crm)")


def test_closure_delete_snapshot_reaches_clone_and_its_host(twin, ids):
    imp = by_id(closure(graph(twin), [(ids.clone_parent, "destroyed")]))
    assert imp[ids.clone].mode == "destroyed"
    assert ids.h3 in imp and ids.app_qa in imp
    assert imp[ids.data].mode == "affected"


def test_closure_replica_target_reaches_source_app(twin, ids):
    imp = by_id(closure(graph(twin), [(ids.replica, "destroyed")]))
    assert imp[ids.link].mode == "destroyed" and imp[ids.data].mode == "affected"
    assert ids.app_erp in imp


def test_one_hop_closure_stops_early(twin, ids):
    full = by_id(closure(graph(twin), [(ids.repo, "destroyed")]))
    one = by_id(closure(graph(twin), [(ids.repo, "destroyed")], max_hops=1))
    assert ids.app_crm in full and ids.app_crm not in one and ids.crm not in one
    assert ids.crm_backups[0] in one


def test_closure_zone_and_spare(twin, ids):
    g = graph(twin)
    zone = next(z.id for z in twin.state.zones.values() if twin.state.hosts[ids.h3].initiators[0] in z.members)
    imp = by_id(closure(g, [(zone, "destroyed")]))
    assert ids.h3 in imp and ids.dev in imp and ids.app_dev in imp
    imp = by_id(closure(g, [(ids.spare2, "affected")]))
    assert ids.pool2 in imp and ids.dev in imp


def test_one_hop_neighbourhood(twin, ids):
    nb = one_hop_neighbourhood(graph(twin), [ids.replica])
    assert ids.link in nb and ids.pool_dr in nb and ids.data not in nb
