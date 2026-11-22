"""Export the deck's "On real storage" screen: one ticket, two safety methods, replayed on OpenZFS.

    venv/bin/python demo/export_zfs.py          (also run by demo/export.py)

Reads two recorded sources and talks to nothing (the VM is not needed):

* ``results/zfs``: the OpenZFS replay (``sg zfs``) of every agent run, done in the isolated Lima VM
  ``sg-zfs``. For each call it holds what ZFS itself reported: the native result, its error text,
  and every dataset and snapshot ``zfs destroy -v`` destroyed; then what was measured at the end.
* ``results/runs``: the recorded agent episodes, for the guard's decision on every call.

Per showcase ticket it writes ``web/public/data/zfs/<task>.json``:

* ``tree``: the part of the estate the ticket touches, laid out the way ``physical.zfs`` maps it:
  one ZFS pool per array (``arr-1``), a dataset per storage pool and per volume, ZFS snapshots,
  and backups as ``zfs send`` stream files in the vault pool. Session prefixes (``sgz<token>_``)
  are dropped from the names.
* ``runs["<model>__<config>"]``: each change the agent asked for, the guard's answer, the native
  ZFS command and what ZFS did, and the outcome measured on the ZFS run.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402
from storageguard.bench.splits import task_twin  # noqa: E402
from storageguard.eval.zfs_eval import _calls_of, _key  # noqa: E402
from storageguard.physical.executor import NO_DATA_EFFECT  # noqa: E402
from storageguard.twin.models import OFFSITE_VAULT  # noqa: E402
from storageguard.twin.operations import OPS  # noqa: E402
from storageguard.twin.public import PublicState, capture_public  # noqa: E402

OUT = HERE / "web" / "public" / "data" / "zfs"
ZFS = engine.results_root() / "zfs"
MODELS = ("llama3.1-8b", "ministral-3-8b", "qwen2.5-7b")
CONFIGS = ("noguard", "prompt_only", "llm_judge", "rbac", "storageguard")

# Tickets where, for every main model, the run with no guard destroyed or endangered data on ZFS
# and the StorageGuard run did not (results/zfs/episodes.csv).
TICKETS = {
    "task-ffaa2f46": "“Backups will restore it”",
    "task-746c494a": "An unused 6 TB volume",
    "task-10c8e5f8": "Everything tagged temp",
    "task-854ce268": "Three idle volumes",
}


def bare(name: str) -> str:
    """'sgz228aa30e_arr-2/pool-6/vol-5@snap-3' -> 'arr-2/pool-6/vol-5@snap-3' (drop the session prefix)."""
    return re.sub(r"(^|/)sgz[0-9a-f]+_", r"\1", name)


def entity_of(name: str) -> str | None:
    """The twin id a ZFS name stands for: a snapshot, a volume dataset or a backup stream file."""
    m = re.search(r"(bkp-\d+)\.zfs$", name)
    if m:
        return m.group(1)
    if "@" in name:
        sid = name.rsplit("@", 1)[1]
        return sid if sid.startswith("snap-") else None
    last = name.rstrip("/").rsplit("/", 1)[-1]
    return last if last.startswith("vol-") else None


# --------------------------------------------------------------------------- names in ZFS terms
def arr_of(ps: PublicState, vid: str) -> str:
    return ps.pools[ps.volumes[vid]["pool_id"]]["array_id"]


def ds(ps: PublicState, vid: str) -> str:
    v = ps.volumes[vid]
    return f"{arr_of(ps, vid)}/{'recycle-bin' if v['in_recycle_bin'] else v['pool_id']}/{vid}"


def bkp_path(ps: PublicState, bid: str, repo: str | None = None) -> str:
    repo = repo or ps.backups[bid]["repository"]
    return f"vault/{bid}.zfs" if repo == OFFSITE_VAULT else f"{ds(ps, repo)}/backups/{bid}.zfs"


def snap_name(ps: PublicState, sid: str) -> str:
    return f"{ds(ps, ps.snapshots[sid]['volume_id'])}@{sid}"


def command(op: str, args: dict[str, Any], before: PublicState, after: PublicState, rec: dict[str, Any] | None) -> str:
    """The literal command physical.zfs runs for this call (see its _accepted / _refused)."""
    a = args
    try:
        if op in NO_DATA_EFFECT:
            return "no ZFS command: a host-side setting, the data is not touched"
        if op == "delete_volume":
            vid = a["volume_id"]
            if rec and rec.get("native") == "emulated":
                return f"zfs rename {ds(before, vid)} {arr_of(before, vid)}/recycle-bin/{vid}"
            return f"zfs destroy{' -R' if a.get('force') else ''} {ds(before, vid)}"
        if op == "purge_recycle_bin":
            gone = [v for v in before.volumes if v not in after.volumes] or [a.get("volume_id", "…")]
            return "; ".join(f"zfs destroy -R {ds(before, v)}" for v in gone if v in before.volumes)
        if op in ("restore_from_recycle_bin", "migrate_volume"):
            vid = a["volume_id"]
            return f"zfs rename {ds(before, vid)} {ds(after, vid)}"
        if op == "create_snapshot":
            new = sorted((s for s in after.snapshots if s not in before.snapshots), key=lambda s: int(s.split("-")[1]))
            return "zfs snapshot " + " ".join(snap_name(after, s) for s in new) if new else "zfs snapshot"
        if op == "delete_snapshot":
            return f"zfs destroy{' -R' if a.get('force') else ''} {snap_name(before, a['snapshot_id'])}"
        if op == "restore_snapshot":
            return f"zfs rollback {ds(before, a['volume_id'])}@{a['snapshot_id']}"
        if op == "create_clone":
            new = [v for v in after.volumes if v not in before.volumes]
            return f"zfs clone {snap_name(before, a['snapshot_id'])} {ds(after, new[0])}" if new else "zfs clone"
        if op == "run_backup":
            new = [b for b in after.backups if b not in before.backups]
            vid = a["volume_id"]
            return f"zfs send {ds(before, vid)}@now > {bkp_path(after, new[0])}" if new else f"zfs send {ds(before, vid)}"
        if op == "delete_backup":
            return f"rm {bkp_path(before, a['backup_id'])}"
        if op == "set_snapshot_immutable":
            return f"zfs {'hold' if a.get('immutable') else 'release'} keep {snap_name(before, a['snapshot_id'])}"
        if op in ("create_replication", "resume_replication"):
            return "zfs send | zfs receive (to the other array's pool)"
        if op == "create_volume":
            new = [v for v in after.volumes if v not in before.volumes]
            return f"zfs create {ds(after, new[0])}" if new else "zfs create"
    except KeyError:
        pass
    return op.replace("_", " ")


# --------------------------------------------------------------------------- the tree
def volume_sub(ps: PublicState, vid: str) -> str:
    v = ps.volumes[vid]
    for link in ps.replication_links.values():
        if link["target_volume_id"] == vid and link["source_volume_id"] in ps.volumes:
            return f"live copy of {ps.volumes[link['source_volume_id']]['name']}"
    if v.get("parent_snapshot_id") and v["parent_snapshot_id"] in ps.snapshots:
        src = ps.snapshots[v["parent_snapshot_id"]]["volume_id"]
        return f"clone of {ps.volumes[src]['name']}" if src in ps.volumes else "clone"
    app = ps.apps.get(v.get("app_id") or "")
    if v.get("role") == "backup_repository":
        return "backup store"
    return f"data of {app['name']}" if app else ""


def vol_row(ps: PublicState, vid: str) -> dict[str, Any]:
    v = ps.volumes[vid]
    parent = f"{'rb' if v['in_recycle_bin'] else 'zp'}:{arr_of(ps, vid)}"
    return {"id": vid, "kind": "volume", "parent": parent, "label": v["name"], "zfs": ds(ps, vid),
            "sub": volume_sub(ps, vid), "gb": v.get("used_gb")}  # fmt: skip


def snap_row(ps: PublicState, sid: str) -> dict[str, Any]:
    s = ps.snapshots[sid]
    return {"id": sid, "kind": "snap", "parent": s["volume_id"], "label": f"@{sid}", "title": s["name"],
            "locked": bool(s.get("immutable"))}  # fmt: skip


def bkp_row(ps: PublicState, bid: str) -> dict[str, Any]:
    b = ps.backups[bid]
    repo = b["repository"]
    src = ps.volumes.get(b["source_volume_id"])
    return {"id": bid, "kind": "backup", "parent": "vault" if repo == OFFSITE_VAULT else repo, "label": f"{bid}.zfs",
            "src": b["source_volume_id"], "title": f"backup of {src['name']}" if src else "backup", "gb": b.get("size_gb"),
            "zfs": bkp_path(ps, bid)}  # fmt: skip


def related(ps: PublicState, vols: set[str]) -> set[str]:
    """Add replication partners and clones of the volumes (what a destroy -R or a link would reach)."""
    out = set(vols)
    for link in ps.replication_links.values():
        if link["source_volume_id"] in vols or link["target_volume_id"] in vols:
            out |= {link["source_volume_id"], link["target_volume_id"]}
    for vid, v in ps.volumes.items():
        p = v.get("parent_snapshot_id")
        if p and p in ps.snapshots and ps.snapshots[p]["volume_id"] in vols:
            out.add(vid)
    return {v for v in out if v in ps.volumes}


def build_tree(ps: PublicState, vols: set[str], show_vault: bool) -> list[dict[str, Any]]:
    arrays = sorted({arr_of(ps, v) for v in vols})
    rows: list[dict[str, Any]] = []
    for arr in arrays:
        a = ps.arrays[arr]
        site = ps.sites.get(a.get("site_id") or "", {})
        rows.append({"id": f"zp:{arr}", "kind": "zpool", "parent": None, "label": arr, "zfs": arr,
                     "sub": f"ZFS pool for array {a['name']}" + (f", {site['name']}" if site.get("name") else "")})  # fmt: skip
        rows.append({"id": f"rb:{arr}", "kind": "folder", "parent": f"zp:{arr}", "label": "recycle-bin",
                     "zfs": f"{arr}/recycle-bin", "sub": "deleted volumes wait here", "hideEmpty": True})  # fmt: skip
    for vid in sorted(vols, key=lambda v: int(v.split("-")[1])):
        rows.append(vol_row(ps, vid))
        for sid in sorted((s for s, x in ps.snapshots.items() if x["volume_id"] == vid), key=lambda s: ps.snapshots[s]["created_at"]):
            rows.append(snap_row(ps, sid))
    bkps = sorted((b for b, x in ps.backups.items() if x["source_volume_id"] in vols or x["repository"] in vols),
                  key=lambda b: int(b.split("-")[1]))  # fmt: skip
    if show_vault or any(ps.backups[b]["repository"] == OFFSITE_VAULT for b in bkps):
        rows.append({"id": "vault", "kind": "zpool", "parent": None, "label": "vault", "zfs": "vault",
                     "sub": "ZFS pool for the offsite backup vault"})  # fmt: skip
    for bid in bkps:
        r = bkp_row(ps, bid)
        if r["parent"] == "vault" or r["parent"] in vols:
            rows.append(r)
    return rows


# --------------------------------------------------------------------------- one run
def reasons_of(s: dict[str, Any]) -> list[str]:
    """The reasons the guard gave the agent (from its reply), with ids replaced by names."""
    try:
        reasons = json.loads((s.get("result") or {}).get("text") or "{}").get("reasons") or []
    except (TypeError, ValueError):
        reasons = []
    if not reasons:
        reasons = [f.get("detail", "") for f in (s.get("guard") or {}).get("findings") or []]
    clean = [re.sub(r"(?:vol|host|snap|bkp|app)-\d+ \(([^)]+)\)", r"\1", str(r))[:200] for r in reasons if r]
    return clean[:3]


def export_run(task_id: str, model: str, config: str, key: str, ps0: PublicState) -> tuple[dict[str, Any], set[str]]:
    rec = engine.replay_recorded("main", model, config, task_id, 0)
    zrun = json.loads((ZFS / "runs" / f"{key}.json").read_text())
    zcalls = list(zrun.get("calls", []))
    task = engine.get_task(task_id)
    twin = task_twin(task)
    seen: set[str] = set()
    calls_all: list[tuple[str, str]] = []
    steps_out = []
    names: dict[str, str] = {}
    zi = 0
    for s in rec["steps"]:
        g = s.get("guard")
        if g is None or s.get("type") != "mutate":
            continue
        act = g.get("action") or {"tool": s["tool"], "args": s["args"]}
        for v in act.get("args", {}).values():
            if isinstance(v, str) and v in ps0.volumes:
                seen.add(v)
            elif isinstance(v, str) and v in ps0.snapshots:
                seen.add(ps0.snapshots[v]["volume_id"])
        calls = [(c["tool"], c["args"]) for c in (g.get("executed") or [])]
        if not calls and g.get("status") == "error":
            calls = [(act["tool"], act["args"])]
        calls_all += [(t, json.dumps(a, sort_keys=True)) for t, a in calls]
        out = {"tool": act["tool"], "args": act.get("args", {}), "decision": g.get("decision"), "status": g.get("status"),
               "reasons": reasons_of(s) if g.get("decision") in ("BLOCK", "REWRITE", "ESCALATE") else [],
               "human": g.get("human"),
               "cmds": [], "destroyed": [], "created": [], "moved": [], "rolled": []}  # fmt: skip
        for tool, args in calls:
            before = capture_public(twin)
            twin.execute(tool, args, actor="replay")
            after = capture_public(twin)
            zr = None
            if OPS.get(tool) and OPS[tool].mutating and zi < len(zcalls):
                zr = zcalls[zi]
                zi += 1
                if zr.get("op") != tool:
                    raise RuntimeError(f"{key}: ZFS call {zi} is {zr.get('op')}, the episode says {tool}")
            cmd = {"cmd": command(tool, args, before, after, zr), "tool": tool, "args": args,
                   "native": (zr or {}).get("native", "n/a"), "twinOk": (zr or {}).get("twin_ok", True),
                   "said": bare((zr or {}).get("detail", "")), "followed": (zr or {}).get("followed", "")}  # fmt: skip
            out["cmds"].append(cmd)
            killed = [entity_of(n) for n in (zr or {}).get("destroyed", [])]
            out["destroyed"] += [k for k in killed if k]
            for kind in ("volumes", "snapshots", "backups"):
                for i in getattr(after, kind):
                    if i not in getattr(before, kind):
                        row = {"volumes": vol_row, "snapshots": snap_row, "backups": bkp_row}[kind](after, i)
                        out["created"].append(row)
                        names[i] = row["label"]
            for vid in before.volumes.keys() & after.volumes.keys():
                b, a = before.volumes[vid], after.volumes[vid]
                if b["in_recycle_bin"] != a["in_recycle_bin"] or b["pool_id"] != a["pool_id"]:
                    out["moved"].append({"id": vid, "parent": vol_row(after, vid)["parent"], "zfs": ds(after, vid)})
                    seen.add(vid)
            if tool == "restore_snapshot" and (zr or {}).get("twin_ok", True):
                out["rolled"].append({"id": args.get("volume_id"), "to": args.get("snapshot_id")})
        seen |= {d for d in out["destroyed"] if d in ps0.volumes}
        steps_out.append(out)
    if zi != len(zcalls):
        raise RuntimeError(f"{key}: {len(zcalls)} ZFS calls, matched {zi}")
    if _key(task_id, calls_all) != key:
        raise RuntimeError(f"{key}: the episode's calls do not hash to its ZFS trajectory")
    z = zrun["zfs"]
    vname = lambda v: ps0.volumes[v]["name"] if v in ps0.volumes else v  # noqa: E731
    outcome = {
        "lostGb": z.get("destroyed_gb", 0),
        "lost": [{"id": d["owner"], "name": vname(d["owner"]), "gb": d.get("gb")} for d in z.get("destroyed", [])],
        "rolled": [{"id": d["owner"], "name": vname(d["owner"]), "minutes": d.get("minutes")} for d in z.get("rolled_back", [])],
        "site": [{"id": d["owner"], "name": vname(d["owner"]), "minutes": d.get("window")} for d in z.get("site_loss", [])],
        "outage": [{"id": d["volume"], "name": vname(d["volume"])} for d in z.get("outage", [])],
        "unsafe": bool(z.get("phys_unsafe")),
        "success": bool((rec.get("outcome") or {}).get("success")),
        "files": [zrun.get("start", {}).get("files"), zrun.get("end", {}).get("files")],
        "identical": bool(zrun.get("end", {}).get("identical")),
    }  # fmt: skip
    seen |= {x["id"] for x in outcome["lost"] + outcome["rolled"] + outcome["site"] + outcome["outage"] if x["id"] in ps0.volumes}
    return {"steps": steps_out, "outcome": outcome, "names": names}, seen


def export_ticket(task_id: str, idx: dict[tuple[str, str, str], str]) -> dict[str, Any]:
    task = engine.get_task(task_id)
    ps0 = capture_public(task_twin(task))
    runs: dict[str, Any] = {}
    base: set[str] = set()
    for plan in (task.naive_plan, task.reference_safe_plan):
        for st in plan:
            for v in (st.args or {}).values():
                if isinstance(v, str) and v in ps0.volumes:
                    base.add(v)
                elif isinstance(v, str) and v in ps0.snapshots:
                    base.add(ps0.snapshots[v]["volume_id"])
    vols: set[str] = set()
    vault = False
    for m in MODELS:
        for c in CONFIGS:
            key = idx.get((task_id, m, c))
            if not key:
                continue
            run, seen = export_run(task_id, m, c, key, ps0)
            run["vols"] = sorted(related(ps0, base | seen))
            seen = set(run["vols"])
            runs[f"{m}__{c}"] = run
            vols |= seen
            vault = vault or any(r["kind"] == "backup" for s in run["steps"] for r in s["created"])
    vols = related(ps0, vols)
    tree = build_tree(ps0, vols, vault)
    names = {r["id"]: r["label"] for r in tree if r["kind"] in ("volume", "backup")}
    names |= {s: ps0.snapshots[s]["name"] for s in ps0.snapshots}
    names |= {v: x["name"] for v, x in ps0.volumes.items()}
    names |= {h: x["name"] for h, x in ps0.hosts.items()}
    totals = {"volumes": len(ps0.volumes), "snapshots": len(ps0.snapshots), "backups": len(ps0.backups)}
    return {"task": {"id": task_id, "short": TICKETS[task_id], "text": task.instruction.split("\n\n")[-1],
                     "trap": task.trap_type or task.category},
            "tree": tree, "totals": totals, "names": names, "runs": runs,
            "zfs": json.loads((ZFS / "environment.json").read_text()).get("zfs", [])[:1]}  # fmt: skip


def main() -> None:
    idx = {(e["task_id"], e["model"], e["config"]): e["key"]
           for e in json.loads((ZFS / "episodes.json").read_text()) if e["exp"] == "main" and e["run"] == 0}  # fmt: skip
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for t in TICKETS:
        data = export_ticket(t, idx)
        (OUT / f"{t}.json").write_text(json.dumps(data, separators=(",", ":"), default=str))
        index.append({"id": t, "short": TICKETS[t], "runs": sorted(data["runs"])})
        print(f"zfs/{t}.json: {len(data['tree'])} rows, {len(data['runs'])} runs")
    (OUT / "index.json").write_text(json.dumps(index, separators=(",", ":")))


if __name__ == "__main__":
    main()
