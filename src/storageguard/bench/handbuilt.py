"""A small hand-built estate used by unit tests, docs and the demo.

It contains one instance of every dependency the guard reasons about, with
ids exposed on a namespace so tests read naturally (``ids.data``, ``ids.repo``).
"""

from __future__ import annotations

from types import SimpleNamespace

from ..twin.models import OFFSITE_VAULT
from .scenario import DAY, HOUR, ScenarioBuilder


def mini_estate() -> tuple[ScenarioBuilder, SimpleNamespace]:
    b = ScenarioBuilder()
    ids = SimpleNamespace()
    sp, sd = b.site("blr-dc1", "primary"), b.site("hyd-dr1", "dr")
    ap, ad = b.array(sp.id, "blr-fa01"), b.array(sd.id, "hyd-fa01")
    ids.arr_p, ids.arr_d = ap.id, ad.id
    ids.pool1 = b.pool(ap.id, "blr-nvme-p1", "RAID6", 20000, 8, 2).id
    ids.pool2 = b.pool(ap.id, "blr-sas-p2", "RAID5", 6000, 8, 1).id
    ids.pool_dr = b.pool(ad.id, "hyd-ssd-p1", "RAID6", 20000, 8, 2).id
    ids.cl_prod = b.cluster("ora-rac-01", "finance").id
    ids.cl_dev = b.cluster("esx-dev-02", "retail").id
    ids.app_erp = b.app("ora-erp", 1, ids.cl_prod).id
    ids.app_crm = b.app("sql-crm", 2, ids.cl_prod).id
    ids.app_dev = b.app("dev-web", 3, ids.cl_dev).id
    ids.app_qa = b.app("qa-erp", 3, ids.cl_dev).id
    ids.h1 = b.host("ora1-n01", ids.cl_prod, [ap.id]).id
    ids.h2 = b.host("ora1-n02", ids.cl_prod, [ap.id]).id
    ids.h3 = b.host("esx2-n01", ids.cl_dev, [ap.id]).id

    data = b.volume("ora-erp-data-01", ids.pool1, 2000, 1200, ids.app_erp, iops=9000)
    redo = b.volume("ora-erp-redo-01", ids.pool1, 200, 80, ids.app_erp, iops=9000)
    ids.data, ids.redo = data.id, redo.id
    ids.cg = b.cg("cg-ora-erp", ids.app_erp, [data.id, redo.id]).id
    for v in (data, redo):
        b.map(v.id, [ids.h1, ids.h2])
    ids.cg_sets = []
    for age in (2 * DAY, DAY, 2 * HOUR):
        ids.cg_sets.append([b.snapshot(v.id, age, cg_id=ids.cg).id for v in (data, redo)])
    ids.link = b.replicate(data.id, ids.pool_dr, "sync").id
    ids.replica = b.s.replication_links[ids.link].target_volume_id
    b.backup(data.id, OFFSITE_VAULT, DAY)

    crm = b.volume("sql-crm-data", ids.pool1, 1000, 600, ids.app_crm, iops=1500)
    ids.crm = crm.id
    b.map(crm.id, [ids.h1, ids.h2])
    ids.crm_snap = b.snapshot(crm.id, DAY).id
    repo = b.volume("bkp-repo-01", ids.pool2, 3000, 100, role="backup_repository", idle_days=1)
    ids.repo = repo.id
    ids.crm_backups = [b.backup(crm.id, repo.id, n * DAY).id for n in (1, 2)]

    dev = b.volume("dev-web-01", ids.pool2, 500, 200, ids.app_dev, iops=120)
    ids.dev = dev.id
    b.map(dev.id, [ids.h3])
    ids.dev_snap = b.snapshot(dev.id, DAY).id

    ids.clone_parent = ids.cg_sets[0][0]
    clone = b.clone(ids.clone_parent, "qa-erp-refresh-01", ids.app_qa, iops=200)
    ids.clone = clone.id
    b.map(clone.id, [ids.h3])

    ids.scratch = b.volume("tmp-migrate-01", ids.pool2, 300, 150, role="scratch", idle_days=120, tags=["temp"]).id
    ids.spare1 = b.s.pools[ids.pool1].hot_spare_ids[0]
    ids.spare2 = b.s.pools[ids.pool2].hot_spare_ids[0]
    b.alert("Scheduled snapshot policy completed.", "info")
    return b, ids
