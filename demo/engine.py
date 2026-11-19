"""Engine behind the presentation app.

Runs scripted plans through the real guard (live), replays recorded LLM episodes, builds the
focused estate map a screen draws, and runs plans on a throwaway file sandbox. It imports the
``storageguard`` package read-only: nothing here changes how the guard or the oracle decide.

Real files are only ever touched through ``storageguard.physical`` sandboxes (``sg-sandbox-*``
temporary directories), which are destroyed when a run ends.
"""

from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from storageguard.agent.llm_client import ScriptedClient
from storageguard.agent.loop import run_episode
from storageguard.bench.schema import Task
from storageguard.bench.splits import load_bench, task_twin
from storageguard.eval.baselines import LABELS, make_middleware, parse_config
from storageguard.eval.harm_oracle import dry_run_harms
from storageguard.eval.human_sim import SimulatedHuman
from storageguard.eval.metrics import NOT_EXECUTED, score_episode
from storageguard.graph.builder import build_graph
from storageguard.graph.closure import closure
from storageguard.guard.analyzers import Action
from storageguard.guard.config import GuardConfig
from storageguard.guard.policy import action_starts
from storageguard.paths import results_dir
from storageguard.twin.operations import OPS
from storageguard.twin.public import PublicState, capture_public

LIVE_CONFIGS = ("noguard", "prompt_only", "rbac", "storageguard")
RECORDED_EXPERIMENTS = ("main", "ablations", "posthoc")
MAP_LIMIT = 46
_lock = threading.Lock()  # the package is not written for concurrent episodes on shared caches


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def tasks() -> dict[str, Task]:
    return {t.id: t for t in load_bench()}


def get_task(task_id: str) -> Task:
    t = tasks().get(task_id)
    if t is None:
        raise KeyError(f"unknown task '{task_id}'")
    return t


def task_summary(t: Task) -> dict[str, Any]:
    return {
        "id": t.id, "category": t.category, "trap": t.trap_type or "", "split": t.split,
        "difficulty": t.difficulty, "expected": t.expected_outcome, "instruction": t.instruction,
        "notes": t.notes, "task_type": t.task_type,
        "naive_plan": [s.model_dump() for s in t.naive_plan],
        "reference_plan": [s.model_dump() for s in t.reference_safe_plan],
    }  # fmt: skip


def all_task_summaries() -> list[dict[str, Any]]:
    return [task_summary(t) for t in tasks().values()]


# --------------------------------------------------------------------------- #
# trajectories: one ordered list of agent calls, each with the guard's decision and result
# --------------------------------------------------------------------------- #


def _jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str))


def _call_type(tool: str) -> str:
    if tool == "finish":
        return "finish"
    if tool == "ask_human":
        return "ask"
    spec = OPS.get(tool)
    if spec is None:
        return "unknown"
    return "mutate" if spec.mutating else "read"


def _parse(content: str) -> Any:
    try:
        return json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None


def _result_preview(tool: str, content: str) -> dict[str, Any]:
    """Keep what a screen shows: full payloads for changes, a count and a short excerpt for reads."""
    obj = _parse(content)
    out: dict[str, Any] = {"text": content[:900] + ("…" if len(content) > 900 else "")}
    if _call_type(tool) != "read" and obj is not None and len(content) < 12000:
        out["data"] = obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list):
                out["count"], out["of"] = len(v), k
                break
    elif isinstance(obj, list):
        out["count"] = len(obj)
    return out


def _slim_guard(d: dict[str, Any]) -> dict[str, Any]:
    det = d.get("detail") or {}
    return {
        "action": d.get("action"), "decision": d.get("decision"), "status": d.get("status"),
        "executed": d.get("executed") or [], "human": d.get("human"), "latency_ms": d.get("latency_ms"),
        "risk": det.get("risk"), "rclass": det.get("rclass"), "severities": det.get("severities") or {},
        "findings": (det.get("findings") or [])[:8], "invariants": det.get("invariants") or [],
        "rewrite": det.get("rewrite"), "rewrite_risk": det.get("rewrite_risk"),
        "judge": det.get("verdict") or det.get("judge"), "reason": det.get("reason"),
    }  # fmt: skip


def build_steps(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Pair every agent tool call with the guard event and the tool result that followed it."""
    steps: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []
    for kind, d in events:
        if kind == "llm":
            calls = d.get("tool_calls") or []
            if not calls and d.get("content"):
                steps.append({"step": d.get("step"), "type": "say", "tool": "", "args": {}, "text": str(d["content"])[:600]})
            for c in calls:
                args = c.get("arguments")
                s = {"step": d.get("step"), "tool": c.get("name", ""), "args": args if isinstance(args, dict) else {},
                     "type": _call_type(c.get("name", ""))}  # fmt: skip
                steps.append(s)
                queue.append(s)
        elif kind == "guard":
            tool = (d.get("action") or {}).get("tool")
            tgt = next((s for s in queue if "guard" not in s and "result" not in s and s["tool"] == tool), None)
            if tgt is None:
                tgt = {"step": d.get("step"), "tool": tool, "args": (d.get("action") or {}).get("args", {}), "type": "mutate"}
                steps.append(tgt)
                queue.append(tgt)
            tgt["guard"] = _slim_guard(d)
            tgt["args"] = (d.get("action") or {}).get("args", tgt["args"])  # the validated values the guard judged
        elif kind == "tool_result":
            tool = d.get("tool")
            tgt = next((s for s in queue if "result" not in s and s["tool"] == tool), None)
            if tgt is None:
                continue
            tgt["result"] = _result_preview(tool, str(d.get("content", "")))
            queue.remove(tgt)
    return steps


# --------------------------------------------------------------------------- #
# enrichment: blast radius before each change, what the change did, the map to draw
# --------------------------------------------------------------------------- #

WATCH_FIELDS = (
    "state", "role", "in_recycle_bin", "immutable", "status", "hot_spare_ids", "pool_id", "provisioned_gb",
    "members", "qos_iops_limit", "scan_status",
)  # fmt: skip


def _diff(b: PublicState, a: PublicState) -> dict[str, Any]:
    removed, added, changed = [], [], []
    for kind in PublicState.ENTITY_KINDS:
        bd, ad = getattr(b, kind), getattr(a, kind)
        removed += [i for i in bd if i not in ad]
        added += [i for i in ad if i not in bd]
        for i in bd.keys() & ad.keys():
            f = {k: [bd[i].get(k), ad[i].get(k)] for k in WATCH_FIELDS if bd[i].get(k) != ad[i].get(k)}
            if f:
                changed.append({"id": i, "fields": f})
    bm = {(m["host_id"], m["volume_id"]) for m in b.mappings}
    am = {(m["host_id"], m["volume_id"]) for m in a.mappings}
    return {
        "removed": sorted(removed), "added": sorted(added), "changed": changed,
        "unmapped": sorted(bm - am), "mapped": sorted(am - bm),
    }  # fmt: skip


def _ids_in_args(args: dict[str, Any], ps: PublicState) -> set[str]:
    out = set()
    for v in (args or {}).values():
        for x in v if isinstance(v, list) else [v]:
            if isinstance(x, str) and ps.kind_of(x):
                out.add(x)
    return out


def enrich(task: Task, steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replay the executed calls on a fresh twin; annotate each guarded call. Returns (steps, map)."""
    twin = task_twin(task)
    initial = twin.state.fast_copy()
    ps0 = capture_public(twin)
    states = [ps0]
    core: set[str] = set()
    targets: set[str] = set()
    for plan in (task.naive_plan, task.reference_safe_plan):
        for s in plan:
            targets |= _ids_in_args(s.args, ps0)
    for s in steps:
        g = s.get("guard")
        if g is None:
            continue
        act = g.get("action") or {"tool": s["tool"], "args": s["args"]}
        before = capture_public(twin)
        try:
            imp = closure(build_graph(before), action_starts(Action(act["tool"], act["args"]), before))
        except Exception:  # noqa: BLE001 - an exotic call must not break the screen
            imp = []
        g["impacted"] = [{"id": i.entity, "kind": i.kind, "effect": i.mode, "hops": i.hops, "path": i.path}
                         for i in imp[:60]]  # fmt: skip
        g["targets"] = sorted(_ids_in_args(act["args"], before))
        targets |= set(g["targets"])
        core |= {i.entity for i in imp if i.hops <= 3}
        if g.get("status") in NOT_EXECUTED:
            harms = dry_run_harms(twin, initial, [(act["tool"], act["args"])], task.allowed_changes)
            g["would_harm"] = bool(harms)
            g["would_harm_codes"] = sorted({f"{h.type}:{h.code}" for h in harms})
        for ex in g.get("executed") or []:
            twin.execute(ex["tool"], ex["args"], actor="replay")
        after = capture_public(twin)
        s["changes"] = _diff(before, after)
        core |= set(s["changes"]["added"]) | set(s["changes"]["removed"])
        states.append(after)
    return steps, {"targets": sorted(targets), "core": sorted(core), "states": states, "final": twin}


# --------------------------------------------------------------------------- #
# the estate map: a focused neighbourhood, laid out by the frontend
# --------------------------------------------------------------------------- #

_VOL_ROLE_KIND = {"backup_repository": "backup_repository"}


def _kind(ps: PublicState, i: str) -> str | None:
    k = ps.kind_of(i)
    if k == "volumes":
        v = ps.volumes[i]
        if v.get("role") == "backup_repository":
            return "backup_repository"
        return "clone" if v.get("parent_snapshot_id") else "volume"
    return {
        "snapshots": "snapshot", "backups": "backup", "replication_links": "replication_link", "apps": "app",
        "hosts": "host", "pools": "pool", "disks": "disk", "consistency_groups": "consistency_group",
        "zones": "zone", "clusters": "cluster", "arrays": "array", "sites": "site",
    }.get(k or "")  # fmt: skip


def _site_of(ps: PublicState, i: str, depth: int = 0) -> str | None:
    if depth > 4:
        return None
    k = ps.kind_of(i)
    e = ps.get(i) or {}
    if k == "pools":
        return (ps.arrays.get(e.get("array_id", "")) or {}).get("site_id")
    if k == "disks":
        return _site_of(ps, e.get("pool_id") or "", depth + 1)
    if k == "volumes":
        return _site_of(ps, e.get("pool_id") or "", depth + 1)
    if k == "snapshots":
        return _site_of(ps, e.get("volume_id") or "", depth + 1)
    if k == "backups":
        repo = e.get("repository")
        return "offsite" if repo == "offsite-vault" else _site_of(ps, repo or "", depth + 1)
    if k == "replication_links":
        return _site_of(ps, e.get("source_volume_id") or "", depth + 1)
    if k == "consistency_groups":
        vids = e.get("volume_ids") or []
        return _site_of(ps, vids[0], depth + 1) if vids else None
    if k == "hosts":
        for m in ps.mappings:
            if m["host_id"] == i:
                return _site_of(ps, m["volume_id"], depth + 1)
        return None
    if k == "apps":
        for v in ps.volumes.values():
            if v.get("app_id") == i and v.get("role") == "primary":
                return _site_of(ps, v["id"], depth + 1)
        return None
    if k == "zones":
        return None
    return None


def _days(ps: PublicState, iso: str | None) -> float | None:
    from storageguard.twin.public import parse_iso

    if not iso:
        return None
    try:
        return round((parse_iso(ps.time) - parse_iso(iso)) / 1440, 1)
    except Exception:  # noqa: BLE001
        return None


def _node(ps: PublicState, i: str) -> dict[str, Any] | None:
    kind = _kind(ps, i)
    if kind is None:
        if i == "offsite-vault":
            return {"id": i, "kind": "offsite_vault", "name": "offsite vault", "site": "offsite", "facts": {}}
        return None
    e = ps.get(i) or {}
    n: dict[str, Any] = {"id": i, "kind": kind, "name": e.get("name") or "", "site": _site_of(ps, i)}
    f: dict[str, Any] = {}
    if kind in ("volume", "clone", "backup_repository"):
        app = ps.apps.get(e.get("app_id") or "")
        f |= {"role": e.get("role"), "gb": e.get("provisioned_gb"), "used_gb": e.get("used_gb"),
              "iops_7d": e.get("iops_7d"), "idle_days": _days(ps, e.get("last_io_at")),
              "tags": e.get("tags") or [], "description": e.get("description") or "",
              "in_recycle_bin": e.get("in_recycle_bin"), "app": app["name"] if app else None,
              "tier": app["tier"] if app else None, "hosts": sum(1 for m in ps.mappings if m["volume_id"] == i),
              "parent_snapshot": e.get("parent_snapshot_id")}  # fmt: skip
        n["tier"] = f["tier"]
    elif kind == "snapshot":
        vol = ps.volumes.get(e.get("volume_id") or "") or {}
        f |= {"volume": e.get("volume_id"), "volume_name": vol.get("name"), "created": e.get("created_at"),
              "age_days": _days(ps, e.get("created_at")), "immutable": e.get("immutable"),
              "scan": e.get("scan_status"), "cg": e.get("consistency_group_id"),
              "clones": len(ps.clones_by_snapshot.get(i, [])) if hasattr(ps, "clones_by_snapshot") else 0}  # fmt: skip
    elif kind == "backup":
        f |= {"source": e.get("source_volume_id"), "repository": e.get("repository"), "created": e.get("created_at"),
              "age_days": _days(ps, e.get("created_at")), "immutable": e.get("immutable"), "scan": e.get("scan_status")}  # fmt: skip
    elif kind == "replication_link":
        f |= {"source": e.get("source_volume_id"), "target": e.get("target_volume_id"), "mode": e.get("mode"),
              "rpo_minutes": e.get("rpo_minutes"), "state": e.get("state")}  # fmt: skip
        n["name"] = f"{(ps.volumes.get(e.get('source_volume_id') or '') or {}).get('name', '?')} → " \
                    f"{(ps.volumes.get(e.get('target_volume_id') or '') or {}).get('name', '?')}"
    elif kind == "app":
        f |= {"tier": e.get("tier"), "cluster": (ps.clusters.get(e.get("cluster_id") or "") or {}).get("name"),
              "tenant": (ps.clusters.get(e.get("cluster_id") or "") or {}).get("tenant")}  # fmt: skip
        n["tier"] = e.get("tier")
    elif kind == "host":
        cl = ps.clusters.get(e.get("cluster_id") or "") or {}
        f |= {"status": e.get("status"), "cluster": cl.get("name"), "tenant": cl.get("tenant")}
    elif kind == "pool":
        f |= {"state": e.get("state"), "raid": e.get("raid_level"), "utilisation_pct": e.get("utilisation_pct"),
              "spares": len(e.get("hot_spare_ids") or []), "physical_gb": e.get("physical_gb"),
              "free_gb": e.get("free_gb"), "overcommit_limit": e.get("overcommit_limit"),
              "provisioned_pct": e.get("provisioned_pct")}  # fmt: skip
    elif kind == "disk":
        f |= {"role": e.get("role"), "state": e.get("state"), "pool": e.get("pool_id")}
        n["name"] = n["name"] or i
    elif kind == "consistency_group":
        f |= {"volumes": e.get("volume_ids") or [], "app": (ps.apps.get(e.get("app_id") or "") or {}).get("name")}
    elif kind == "zone":
        f |= {"fabric": e.get("fabric"), "members": len(e.get("members") or [])}
    elif kind == "cluster":
        f |= {"tenant": e.get("tenant")}
    n["facts"] = {k: v for k, v in f.items() if v not in (None, "", [], {})}
    return n


def _neighbours(ps: PublicState, i: str) -> list[str]:
    """Context worth drawing around an entity, most important first."""
    k = ps.kind_of(i)
    e = ps.get(i) or {}
    out: list[str] = []
    if k == "volumes":
        if e.get("app_id"):
            out.append(e["app_id"])
        out += [lk["id"] for lk in ps.replication_links.values() if i in (lk["source_volume_id"], lk["target_volume_id"])]
        out += [lk["target_volume_id"] if lk["source_volume_id"] == i else lk["source_volume_id"]
                for lk in ps.replication_links.values() if i in (lk["source_volume_id"], lk["target_volume_id"])]  # fmt: skip
        if e.get("parent_snapshot_id"):
            out.append(e["parent_snapshot_id"])
        out += [m["host_id"] for m in ps.mappings if m["volume_id"] == i][:3]
        out += [cg["id"] for cg in ps.consistency_groups.values() if i in cg.get("volume_ids", [])]
        snaps = sorted((s for s in ps.snapshots.values() if s["volume_id"] == i), key=lambda s: s["created_at"], reverse=True)
        out += [s["id"] for s in snaps[:2]]
        bk = sorted((b for b in ps.backups.values() if b["source_volume_id"] == i), key=lambda b: b["created_at"], reverse=True)
        out += [b["id"] for b in bk[:1]]
    elif k == "snapshots":
        out.append(e["volume_id"])
        out += [v["id"] for v in ps.volumes.values() if v.get("parent_snapshot_id") == i]
    elif k == "backups":
        out += [e["source_volume_id"], e["repository"]]
    elif k == "replication_links":
        out += [e["source_volume_id"], e["target_volume_id"]]
    elif k == "consistency_groups":
        out += list(e.get("volume_ids", [])) + [e.get("app_id", "")]
    elif k == "hosts":
        out += [m["volume_id"] for m in ps.mappings if m["host_id"] == i][:3]
    elif k == "pools":
        out += list(e.get("hot_spare_ids", []))[:3]
    elif k == "disks":
        if e.get("pool_id"):
            out.append(e["pool_id"])
    elif k == "zones":
        pass
    return [x for x in out if x]


def build_map(ps: PublicState, targets: list[str], core: list[str], extra_states: list[PublicState] | None = None,
              harm_entities: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    """The focused neighbourhood of an episode: targets, their blast radius, and the context around them."""
    merged = ps  # entities created later are looked up in the later states
    later = list(extra_states or [])

    def exists(i: str) -> PublicState | None:
        if i == "offsite-vault":
            return ps
        if ps.kind_of(i):
            return ps
        for s in later:
            if s.kind_of(i):
                return s
        return None

    order: list[str] = []
    seen: set[str] = set()

    def add(i: str) -> None:
        if i and i not in seen and exists(i) is not None:
            seen.add(i)
            order.append(i)

    for i in targets:
        add(i)
    for i in harm_entities or []:
        add(i)
    for i in core:
        add(i)
    frontier = list(order)
    for i in frontier:
        src = exists(i)
        if src is None:
            continue
        for nb in _neighbours(src, i):
            if len(order) >= MAP_LIMIT:
                break
            add(nb)
    # second ring only for the direct targets (keeps the map readable)
    for i in list(targets):
        src = exists(i)
        if src is None:
            continue
        for nb in _neighbours(src, i):
            src2 = exists(nb)
            if src2 is None:
                continue
            for nb2 in _neighbours(src2, nb)[:2]:
                if len(order) >= MAP_LIMIT:
                    break
                if _kind(src2, nb2) in ("app", "volume", "replication_link"):
                    add(nb2)
    nodes = []
    for i in order:
        src = exists(i)
        n = _node(src, i) if src is not None else None
        if n is not None:
            n["created_later"] = src is not ps
            nodes.append(n)
    ids = {n["id"] for n in nodes}
    edges: list[dict[str, str]] = []
    seen_e: set[tuple[str, str, str]] = set()
    for s in [ps, *later]:
        g = build_graph(s)
        for u, v, key in g.edges(keys=True):
            if u in ids and v in ids and key not in ("in_array", "in_cluster") and (u, v, key) not in seen_e:
                seen_e.add((u, v, key))
                edges.append({"from": u, "to": v, "type": key})
    sites = [{"id": s["id"], "name": s["name"], "role": s["role"]} for s in ps.sites.values()]
    return {"nodes": nodes, "edges": edges, "sites": sites, "targets": [t for t in targets if t in ids], "time": ps.time}


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #


def _outcome(row: dict[str, Any]) -> dict[str, Any]:
    keys = ("unsafe", "harm_codes", "goal_met", "success", "finished", "termination", "steps", "n_escalations",
            "n_block", "n_rewrite", "n_allow", "n_approved", "n_denied", "harm_gb", "summary", "H1", "H2", "H3",
            "H4", "H5", "H6", "false_block", "guard_latency_ms_median")  # fmt: skip
    out = {k: row.get(k) for k in keys}
    out["harms"] = row.get("harms") or []
    return _jsonable(out)


def _package(task: Task, steps: list[dict[str, Any]], row: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    steps, info = enrich(task, steps)
    harm_entities = [h.get("entity") for h in (row.get("harms") or []) if h.get("entity")]
    m = build_map(info["states"][0], info["targets"], info["core"], info["states"][1:], harm_entities)
    return _jsonable({"task": task_summary(task), "meta": meta, "steps": steps, "outcome": _outcome(row), "map": m})


def guard_config(knobs: dict[str, Any]) -> GuardConfig:
    over: dict[str, Any] = {}
    if knobs.get("tau_allow") is not None:
        over["tau_allow"] = float(knobs["tau_allow"])
    if knobs.get("tau_block") is not None:
        over["tau_block"] = float(knobs["tau_block"])
    for k, field in (("one_hop", "one_hop_only"), ("pit", "pit_check")):
        if k in knobs:
            over[field] = bool(knobs[k])
    for k, field in (("reversibility", "use_reversibility"), ("rewrites", "enable_rewrites"),
                     ("feedback", "structured_feedback")):  # fmt: skip
        if k in knobs:
            over[field] = bool(knobs[k])
    return GuardConfig.load().with_overrides(**over)


def run_live(task_id: str, agent: str = "scripted:naive", config: str = "storageguard",
             knobs: dict[str, Any] | None = None, physical: bool = False) -> dict[str, Any]:  # fmt: skip
    """Run one scripted plan through a live middleware. ``physical`` also executes it on real sandbox files."""
    task = get_task(task_id)
    knobs = dict(knobs or {})
    if config not in LIVE_CONFIGS:
        raise ValueError(f"'{config}' cannot run live; choose from {LIVE_CONFIGS} (the LLM judge needs a model)")
    if agent not in ("scripted:naive", "scripted:reference"):
        raise ValueError("live runs use the scripted plans: scripted:naive or scripted:reference")
    human = knobs.get("human", "oracle")
    cs = parse_config(config + (f"@human={human}" if human != "oracle" else ""))
    gcfg = guard_config(knobs)
    plan = task.naive_plan if agent == "scripted:naive" else task.reference_safe_plan
    with _lock:
        twin = task_twin(task)
        session = None
        start_files: list[dict[str, Any]] = []
        if physical:
            from storageguard.physical.session import PhysicalSession

            session = PhysicalSession(twin)
            twin = session.twin
            start_files = [{"path": p, "bytes": session.sb.size(p)} for p in sorted(session.sb.files())]
        try:
            initial = twin.state.fast_copy()
            sim = SimulatedHuman(task, initial, cs.human)
            mw = make_middleware(cs, sim, None, gcfg, {})
            events: list[tuple[str, dict[str, Any]]] = []
            traj = run_episode(task.instruction, twin, ScriptedClient(plan, agent), mw, sim,
                               on_event=lambda k, d: events.append((k, _jsonable(d))))  # fmt: skip
            row = score_episode(task, initial, twin, traj, stops=[], model=agent, config=config, run=0)
            row["summary"] = traj.summary
            phys = None
            if session is not None:
                from storageguard.eval.physical_eval import PhysicalOptions

                executed = [(s["tool"], s["args"]) for e in traj.guard_events for s in e.executed]
                phys = session.finish(task.allowed_changes, PhysicalOptions().reference_for(task, executed))
                phys["start_file_list"] = start_files
        finally:
            if session is not None:
                session.close()
        meta = {"source": "live", "agent": agent, "config": config, "label": LABELS.get(config, config),
                "knobs": knobs, "human": human, "tau_allow": gcfg.tau_allow, "tau_block": gcfg.tau_block}  # fmt: skip
        out = _package(task, build_steps(events), row, meta)
    if phys is not None:
        out["physical"] = _jsonable(_physical_view(phys))
    return out


def _physical_view(p: dict[str, Any]) -> dict[str, Any]:
    keep = ("start_files", "start_bytes", "files_deleted", "bytes_deleted", "files_overwritten", "copies_destroyed",
            "destroyed", "rolled_back", "cg_split", "outage", "site_loss", "ransomware", "ransomware_admin", "drills",
            "phys_data", "phys_outage", "phys_site", "phys_ransom", "phys_unsafe", "destroyed_gb", "lock_mode",
            "drift", "start_file_list")  # fmt: skip
    out = {k: p.get(k) for k in keep}
    out["deletions"] = [{"op": d.get("op"), "action": d.get("action"),
                         "files": [{"path": f["path"], "bytes": f["bytes"], "held": f.get("held", {})} for f in d.get("files", [])]}
                        for d in p.get("deletions", [])]  # fmt: skip
    return out


def recorded_index(task_id: str) -> list[dict[str, Any]]:
    """Which recorded LLM episodes exist for a task."""
    out = []
    for exp in RECORDED_EXPERIMENTS:
        root = results_dir() / "runs" / exp
        if not root.exists():
            continue
        for mdir in sorted(p for p in root.iterdir() if p.is_dir()):
            if mdir.name.startswith("scripted"):
                continue
            for cdir in sorted(p for p in mdir.iterdir() if p.is_dir()):
                runs = sorted(int(p.stem.rsplit("__r", 1)[1]) for p in cdir.glob(f"{task_id}__r*.json"))
                if runs:
                    out.append({"exp": exp, "model": mdir.name, "config": cdir.name, "runs": runs})
    return out


def _safe(part: str) -> str:
    if not part or "/" in part or ".." in part or "\\" in part:
        raise ValueError(f"bad path component '{part}'")
    return part


def replay_recorded(exp: str, model: str, config: str, task_id: str, run: int = 0) -> dict[str, Any]:
    task = get_task(task_id)
    base = results_dir() / "runs" / _safe(exp) / _safe(model) / _safe(config) / f"{_safe(task_id)}__r{int(run)}"
    jl, js = base.with_suffix(".jsonl"), base.with_suffix(".json")
    if not jl.exists() or not js.exists():
        raise KeyError(f"no recorded episode {exp}/{model}/{config}/{task_id} run {run}")
    events = []
    for line in jl.read_text().splitlines():
        if line.strip():
            e = json.loads(line)
            if e.get("type") in ("llm", "guard", "tool_result"):
                events.append((e["type"], e))
    row = json.loads(js.read_text())
    meta = {"source": "recorded", "exp": exp, "model": model, "config": config, "run": int(run),
            "label": LABELS.get(config.split("__")[0], config)}  # fmt: skip
    with _lock:
        return _package(task, build_steps(events), row, meta)


def task_map(task_id: str) -> dict[str, Any]:
    """The starting map for a ticket: the naive plan's targets and its blast radius."""
    task = get_task(task_id)
    with _lock:
        twin = task_twin(task)
        ps = capture_public(twin)
        g = build_graph(ps)
        targets: list[str] = []
        core: set[str] = set()
        for s in task.naive_plan:
            ids = sorted(_ids_in_args(s.args, ps))
            targets += [i for i in ids if i not in targets]
            try:
                core |= {i.entity for i in closure(g, action_starts(Action(s.tool, s.args), ps)) if i.hops <= 3}
            except Exception:  # noqa: BLE001
                pass
        return _jsonable({"task": task_summary(task), "map": build_map(ps, targets, sorted(core))})


# --------------------------------------------------------------------------- #
# policy-level sweep: every test ticket's careless and correct plan, under the chosen knobs
# --------------------------------------------------------------------------- #

_sweep_cache: dict[str, dict[str, Any]] = {}


def sweep(config: str = "storageguard", knobs: dict[str, Any] | None = None) -> dict[str, Any]:
    from storageguard.eval.runner import run_one

    knobs = dict(knobs or {})
    key = json.dumps([config, knobs], sort_keys=True)
    if key in _sweep_cache:
        return _sweep_cache[key]
    human = knobs.get("human", "oracle")
    spec = config + (f"@human={human}" if human != "oracle" else "")
    gcfg = guard_config(knobs)
    test = [t for t in tasks().values() if t.split == "test"]
    res: dict[str, Any] = {"config": config, "knobs": knobs, "tau_allow": gcfg.tau_allow, "tau_block": gcfg.tau_block}
    with _lock:
        for plan in ("naive", "reference"):
            rows = [run_one(t, f"scripted:{plan}", spec, guard_cfg=gcfg) for t in test]
            risky = [r for r in rows if r["category"] in ("trap", "injection")]
            routine = [r for r in rows if r["category"] == "routine"]
            res[plan] = {
                "tasks": len(rows),
                "harm_rate": sum(r["unsafe"] for r in risky) / max(1, len(risky)),
                "success_rate": sum(r["success"] for r in rows) / max(1, len(rows)),
                "false_block_rate": sum(r["false_block"] for r in routine) / max(1, len(routine)),
                "asks_per_task": sum(r["n_escalations"] for r in rows) / max(1, len(rows)),
                "rewrites_per_task": sum(r["n_rewrite"] for r in rows) / max(1, len(rows)),
                "blocks_per_task": sum(r["n_block"] for r in rows) / max(1, len(rows)),
                "harmful_tasks": [r["task_id"] for r in risky if r["unsafe"]],
            }
    _sweep_cache[key] = res
    return res


def results_root() -> Path:
    return results_dir()
