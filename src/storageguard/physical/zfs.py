"""Replay on a real storage system: OpenZFS inside a disposable, isolated Lima VM.

The file-backed sandbox (``executor.py``) shows *what* the twin's operations do to bytes.
This module asks whether a real storage stack agrees: each episode's executed calls are run
against OpenZFS pools, with every operation translated to its most literal native command.

Mapping (one ZFS pool per array, on a sparse file inside the VM):

    array            -> pool  sgz<token>_<array>          (+ sgz<token>_vault: the offsite vault)
    storage pool     -> dataset <pool>/<pool-id>
    volume           -> dataset <pool>/<pool-id>/<vol>  holding data.img (recycle bin: <pool>/recycle-bin/<vol>)
    snapshot         -> ZFS snapshot <vol-dataset>@<snap>     immutable -> ``zfs hold``
    clone            -> ``zfs clone`` of that snapshot
    backup           -> a ``zfs send`` stream file (vault, or <repository volume>/backups/)
                        immutable -> chattr +i;  restore check -> ``zfs receive``
    replication      -> ``zfs send | zfs receive`` to the other array's pool
    restore_snapshot -> ``zfs rollback`` (without -r)
    delete           -> ``zfs destroy`` (-R when the call says force); purge -> ``zfs destroy -R``

For every mutating call the twin *accepted*, the native command is run for real; if ZFS
refuses, that is recorded and the twin's outcome is then reproduced by the nearest equivalent
(release holds and destroy, copy a snapshot's data instead of rolling back, ...), so ZFS keeps
following the control plane.  For a destructive call the twin *refused*, the native command is
tried under a pool checkpoint and rewound, so ZFS's own answer is observed without changing
anything.  After every call the ZFS inventory is checked against the twin's (``drift``), and
at the end every volume, snapshot and backup is read back from ZFS (backups through a real
``zfs receive``) into a fresh file sandbox that the ordinary measurement code then scores.

Safety: the host only ever runs ``limactl``; it refuses unless the VM is in plain mode with no
mounts and no port forwards; the agent inside the VM (``zfs_agent.py``) re-checks that no host
or network file system is visible and confines every name and path to its own session.
"""

from __future__ import annotations

import base64
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..twin.models import OFFSITE_VAULT
from ..twin.operations import OPS
from ..twin.public import capture_public, to_public
from ..twin.state import StorageTwin
from .executor import NO_DATA_EFFECT, PhysicalTwin
from .layout import backup_file, image, snapshot_file, volume_dir
from .measure import _head, read_back
from .sandbox import Sandbox
from .session import PhysicalSession, measure_end

VM = "sg-zfs"
AGENT = Path(__file__).with_name("zfs_agent.py")
DESTRUCTIVE_REFUSABLE = {"delete_volume", "purge_recycle_bin", "delete_snapshot", "restore_snapshot", "delete_backup"}


class ZfsUnavailable(RuntimeError):
    """The VM is missing, not running, or not isolated: nothing was started."""


class ZfsRestart(Exception):  # noqa: N818 - control flow, not an error
    """A native attempt on a call the twin refused changed ZFS: rewind and replay, remembering the verdict."""

    def __init__(self, step: int, record: Any) -> None:
        super().__init__(f"step {step}")
        self.step, self.record = step, record


# --------------------------------------------------------------------------- the connection
def check_vm(name: str = VM) -> dict[str, Any]:
    """Refuse unless the VM runs in plain mode with no mounts and no port forwards."""
    try:
        out = subprocess.run(["limactl", "list", name, "--json"], capture_output=True, text=True, timeout=60, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ZfsUnavailable(f"limactl is not available: {exc}") from exc
    line = next((x for x in out.stdout.splitlines() if x.strip()), "")
    if not line:
        raise ZfsUnavailable(f"no Lima VM named {name!r}")
    info = json.loads(line)
    cfg = info.get("config") or {}
    problems = []
    if info.get("status") != "Running":
        problems.append(f"status {info.get('status')}")
    if cfg.get("mounts"):
        problems.append(f"{len(cfg['mounts'])} host mount(s)")
    if not cfg.get("plain"):
        problems.append("not in plain mode")
    if cfg.get("portForwards"):
        problems.append("port forwards configured")
    if problems:
        raise ZfsUnavailable(f"refusing to use VM {name!r}: " + ", ".join(problems))
    return {k: info.get(k) for k in ("name", "status", "vmType", "cpus", "memory", "disk")}


def _boot(*argv: str) -> str:
    """A ``python3 -c`` program that runs the agent's source (shipped as text, base64-encoded)."""
    code = base64.b64encode(AGENT.read_bytes()).decode()
    return f"import base64,sys;sys.argv+={list(argv)!r};exec(compile(base64.b64decode('{code}'),'zfs_agent.py','exec'))"


class Agent:
    """One agent process inside the VM (one session: its own pools and directory)."""

    def __init__(self, vm: str = VM) -> None:
        self.vm_info = check_vm(vm)
        boot = _boot()
        self.proc = subprocess.Popen(["limactl", "shell", "--workdir", "/", vm, "--", "sudo", "python3", "-u", "-c", boot],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     bufsize=1)  # fmt: skip
        hello = self._readline()
        if not hello.get("ready"):
            self.proc.kill()
            raise ZfsUnavailable(f"the ZFS agent refused to start: {hello.get('fatal') or hello}")
        self.token, self.root = hello["token"], hello["root"]
        self.calls = 0

    def _readline(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read() if self.proc.stderr else ""
            raise ZfsUnavailable(f"the ZFS agent exited: {err.strip()[-500:]}")
        return json.loads(line)

    def __call__(self, op: str, **kw: Any) -> dict[str, Any]:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"op": op, **kw}) + "\n")
        self.proc.stdin.flush()
        self.calls += 1
        res = self._readline()
        if res.get("refused"):
            raise PermissionError(f"ZFS agent refused {op}: {res.get('out')}")
        return res

    def batch(self, items: list[dict[str, Any]], stop: bool = True) -> dict[str, Any]:
        return self("batch", items=items, stop=stop) if items else {"ok": True, "results": []}

    def close(self) -> None:
        try:
            if self.proc.poll() is None and self.proc.stdin is not None:
                self.proc.stdin.write(json.dumps({"op": "exit"}) + "\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


def reap(vm: str = VM) -> list[str]:
    """Destroy pools and directories left by crashed sessions (only ones this tool created)."""
    check_vm(vm)
    boot = _boot("--reap")
    out = subprocess.run(["limactl", "shell", "--workdir", "/", vm, "--", "sudo", "python3", "-c", boot],
                         capture_output=True, text=True, timeout=600, check=False)  # fmt: skip
    line = next((x for x in out.stdout.splitlines() if x.strip()), "{}")
    res = json.loads(line)
    if "fatal" in res:
        raise ZfsUnavailable(res["fatal"])
    return res.get("reaped", [])


# --------------------------------------------------------------------------- names
def _snaps_of(twin: StorageTwin, vid: str) -> list[dict[str, Any]]:
    snaps = [to_public("snapshots", s) for s in twin.state.snapshots.values()]
    return sorted((s for s in snaps if s["volume_id"] == vid), key=lambda s: (s["created_at"], s["id"]))


@dataclass
class CallRecord:
    step: int
    op: str
    twin_ok: bool
    native: str  # ok | refused | emulated | no-data-effect | not-modelled | n/a
    detail: str = ""
    destroyed: list[str] = field(default_factory=list)  # what ZFS reported destroying (zfs destroy -v)
    twin_destroyed: list[str] = field(default_factory=list)  # the twin's cascade, in ZFS names
    would_destroy: list[str] = field(default_factory=list)  # e.g. the newer snapshots ``rollback -r`` needs to destroy
    followed: str = ""  # how ZFS was brought to the twin's outcome after a native refusal

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in ("", [])}


class ZfsExecutor:
    """Mirrors every mutating call of a twin onto OpenZFS and checks the result."""

    def __init__(self, agent: Agent, twin: StorageTwin, pools: dict[str, Any] | None = None,
                 memo: dict[int, CallRecord] | None = None) -> None:  # fmt: skip
        self.a = agent
        st = twin.state
        self.memo = memo or {}  # step -> verdict already observed on an earlier pass (see ZfsRestart)
        self.pool_of: dict[str, str] = {}  # array id -> zfs pool
        self.mnt_of: dict[str, str] = {}  # zfs pool -> its mount (relative to the session root)
        self.where: dict[str, tuple[str, str]] = {}  # volume -> (array, pool id | "recycle-bin")
        for vid in st.volumes:
            self.where[vid] = self._where(twin, vid)
        self.snap_vol = {sid: to_public("snapshots", s)["volume_id"] for sid, s in st.snapshots.items()}
        self.bkp_repo = {bid: to_public("backups", b)["repository"] for bid, b in st.backups.items()}
        self.calls: list[CallRecord] = []
        self.drift: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.step = 0
        self.vault = ""  # mount of the vault pool
        self.vault_pool = self.scratch_pool = ""
        if pools:
            self.pool_of, self.mnt_of = dict(pools["pool_of"]), dict(pools["mnt_of"])
            self.vault, self.vault_pool, self.scratch_pool = pools["vault"], pools["vault_pool"], pools["scratch_pool"]

    def pools(self) -> dict[str, Any]:
        return {"pool_of": self.pool_of, "mnt_of": self.mnt_of, "vault": self.vault, "vault_pool": self.vault_pool,
                "scratch_pool": self.scratch_pool}  # fmt: skip

    # ------------------------------------------------------------------ naming
    @staticmethod
    def _where(twin: StorageTwin, vid: str) -> tuple[str, str]:
        v = to_public("volumes", twin.state.volumes[vid])
        arr = to_public("pools", twin.state.pools[v["pool_id"]])["array_id"]
        return arr, ("recycle-bin" if v["in_recycle_bin"] else v["pool_id"])

    def ds(self, vid: str) -> str:
        arr, sub = self.where[vid]
        return f"{self.pool_of[arr]}/{sub}/{vid}"

    def mnt(self, vid: str) -> str:
        arr, sub = self.where[vid]
        return f"{self.mnt_of[self.pool_of[arr]]}/{sub}/{vid}"

    def img(self, vid: str) -> str:
        return f"{self.mnt(vid)}/data.img"

    def snap(self, sid: str) -> str:
        return f"{self.ds(self.snap_vol[sid])}@{sid}"

    def snap_img(self, sid: str) -> str:
        return f"{self.mnt(self.snap_vol[sid])}/.zfs/snapshot/{sid}/data.img"

    def bkp(self, bid: str, repo: str | None = None) -> str:
        repo = repo or self.bkp_repo[bid]
        return f"{self.vault}/{bid}.zfs" if repo == OFFSITE_VAULT else f"{self.mnt(repo)}/backups/{bid}.zfs"

    # ------------------------------------------------------------------ build the starting world
    def materialise(self, twin: StorageTwin, sb: Sandbox) -> None:
        """Build the starting world on ZFS with exactly the bytes of the file sandbox."""
        st = twin.state
        loc = {vid: volume_dir(st, vid) for vid in st.volumes}
        for arr in sorted(st.arrays):
            r = self.a("pool_create", name=arr)
            if not r["ok"]:
                raise RuntimeError(f"zpool create failed: {r['out']}")
            self.pool_of[arr], self.mnt_of[r["pool"]] = r["pool"], r["mnt"]
        for extra in ("vault", "scratch"):
            r = self.a("pool_create", name=extra)
            if not r["ok"]:
                raise RuntimeError(f"zpool create failed: {r['out']}")
            self.mnt_of[r["pool"]] = r["mnt"]
            setattr(self, f"{extra}_pool", r["pool"])
        self.vault = self.mnt_of[self.vault_pool]
        items: list[dict[str, Any]] = []
        for pid, p in sorted(st.pools.items()):
            items.append({"op": "create", "ds": f"{self.pool_of[to_public('pools', p)['array_id']]}/{pid}"})
        for arr in sorted(st.arrays):
            items.append({"op": "create", "ds": f"{self.pool_of[arr]}/recycle-bin"})
        holds: list[str] = []
        done: set[str] = set()
        pending = sorted(st.volumes, key=lambda v: (st.volumes[v].created_at, v))
        while pending:
            progressed = False
            for vid in list(pending):
                parent = to_public("volumes", st.volumes[vid])["parent_snapshot_id"]
                if parent and self.snap_vol.get(parent) not in done:
                    continue
                if parent:
                    items.append({"op": "clone", "snap": self.snap(parent), "ds": self.ds(vid)})
                else:
                    items.append({"op": "create", "ds": self.ds(vid)})
                for s in _snaps_of(twin, vid):
                    items.append({"op": "write", "path": self.img(vid), "text": sb.read(snapshot_file(loc[vid], s["id"]))})
                    items.append({"op": "snapshot", "snaps": [self.snap(s["id"])]})
                    if s["immutable"]:
                        holds.append(self.snap(s["id"]))
                items.append({"op": "write", "path": self.img(vid), "text": sb.read(image(loc[vid]))})
                done.add(vid)
                pending.remove(vid)
                progressed = True
            if not progressed:
                raise RuntimeError(f"clone parents missing for {pending}")
        if holds:
            items.append({"op": "hold", "snaps": holds})
        stage = f"{self.scratch_pool}/stage"
        stage_mnt = f"{self.mnt_of[self.scratch_pool]}/stage"
        items.append({"op": "create", "ds": stage})
        locks = []
        for bid, b in sorted(st.backups.items()):
            pb = to_public("backups", b)
            if pb["repository"] != OFFSITE_VAULT and pb["repository"] not in st.volumes:
                continue
            items.append({"op": "write", "path": f"{stage_mnt}/data.img", "text": sb.read(backup_file(pb["repository"], bid, loc))})
            items.append({"op": "snapshot", "snaps": [f"{stage}@{bid}"]})
            items.append({"op": "send_file", "snap": f"{stage}@{bid}", "path": self.bkp(bid)})
            if pb["immutable"]:
                locks.append(self.bkp(bid))
        items.append({"op": "destroy", "target": stage, "flags": "r"})
        items += [{"op": "chattr", "path": p, "immutable": True} for p in locks]
        r = self.a.batch(items)
        if not r["ok"]:
            bad = next(x for x in r["results"] if not x.get("ok"))
            raise RuntimeError(f"materialisation failed at item {len(r['results'])}: {bad.get('out')}")

    # ------------------------------------------------------------------ inventory
    def expected(self, twin: StorageTwin) -> tuple[set[str], set[str], set[str], set[str]]:
        st = twin.state
        dss = {self.ds(v) for v in st.volumes}
        snaps = {self.snap(s) for s in st.snapshots}
        held = {self.snap(s) for s, x in st.snapshots.items() if to_public("snapshots", x)["immutable"]}
        files = {self.img(v) for v in st.volumes}
        for bid in st.backups:
            if bid in self.bkp_repo:
                files.add(self.bkp(bid))
        return dss, snaps, held, files

    def actual(self) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
        inv = self.a("inventory")["items"]
        scratch = self.scratch_pool
        dss, snaps, held = set(), set(), set()
        for it in inv:
            name = it["name"]
            if name.split("/")[0] == scratch:
                continue
            if it["type"] == "snapshot":
                snaps.add(name)
                if it.get("userrefs", "0") not in ("0", "-"):
                    held.add(name)
            elif name.count("/") >= 2:
                dss.add(name)
        f = self.a("files")
        smnt = self.mnt_of[scratch] + "/"
        files = {x for x in f["files"] if not x.startswith(smnt)}
        return dss, snaps, held, files, set(f["immutable"])

    def check(self, twin: StorageTwin, op: str) -> None:
        want = self.expected(twin)
        have = self.actual()
        names = ("datasets", "snapshots", "holds", "files")
        diff = {}
        for n, w, h in zip(names, want, have[:4], strict=True):
            if w != h:
                diff[n] = {"missing": sorted(w - h)[:5], "extra": sorted(h - w)[:5]}
        imm_want = {self.bkp(b) for b, x in twin.state.backups.items() if b in self.bkp_repo and to_public("backups", x)["immutable"]}
        if imm_want != have[4]:
            diff["immutable"] = {"missing": sorted(imm_want - have[4]), "extra": sorted(have[4] - imm_want)}
        if diff:
            self.drift.append({"step": self.step, "op": op, **diff})

    # ------------------------------------------------------------------ helpers
    def _destroy(self, target: str, flags: str) -> dict[str, Any]:
        return self.a("destroy", target=target, flags=flags)

    def _force_destroy(self, target: str) -> str:
        """Bring ZFS to the twin's outcome after a refusal: release holds in the way, then destroy -R."""
        inv = self.a("inventory")["items"]
        # the datasets destroy -R removes: the target and, transitively, every clone of their snapshots
        closure = {target} if "@" not in target else set()
        roots = {target} if "@" in target else {target + "@"}
        changed = True
        while changed:
            changed = False
            for it in inv:
                if it["type"] != "filesystem" or it["name"] in closure:
                    continue
                origin = it.get("origin", "-")
                if origin in roots or origin.split("@")[0] in closure:
                    closure.add(it["name"])
                    changed = True
        held = [it["name"] for it in inv if it["type"] == "snapshot" and it.get("userrefs", "0") not in ("0", "-")
                and (it["name"].split("@")[0] in closure or it["name"] == target)]  # fmt: skip
        if held:
            self.a("release", snaps=held)
        r = self._destroy(target, "R")
        return f"released {len(held)} hold(s), destroy -R: {'ok' if r['ok'] else r['out']}"

    def ship(self, src: str, tgt: str) -> str:
        """Replication: a full ``zfs send | zfs receive -F`` of the source onto the target."""
        tmp = f"{self.ds(src)}@sgrepl"
        self.a("snapshot", snaps=[tmp])
        r = self.a("send_recv", snap=tmp, ds=self.ds(tgt), force=True)
        how = "send|recv -F"
        if not r["ok"]:  # e.g. the target has snapshots of its own: ZFS refuses to overwrite it
            text = self.a("read", paths=[self.img(src)])["texts"][self.img(src)]
            self.a("write", path=self.img(tgt), text=text)
            how = f"recv refused ({r['out'].splitlines()[0] if r['out'] else ''}); copied data.img"
        else:
            self._destroy(f"{self.ds(tgt)}@sgrepl", "")
        self._destroy(tmp, "")
        return how

    def _twin_cascade(self, vids: list[str], res: dict[str, Any]) -> list[str]:
        """The datasets and snapshots the twin removed, in ZFS names (before they are forgotten)."""
        out = {self.ds(v) for v in vids if v in self.where}
        out |= {self.snap(s) for s in res.get("deleted_snapshots", []) if s in self.snap_vol}
        return sorted(out)

    def _active_links_from(self, twin: StorageTwin, vid: str) -> list[dict[str, Any]]:
        links = [to_public("replication_links", x) for x in twin.state.replication_links.values()]
        return [x for x in links if x["source_volume_id"] == vid and x["state"] == "active"]

    def _forget(self, vid: str) -> None:
        self.where.pop(vid, None)
        for sid in [s for s, v in self.snap_vol.items() if v == vid]:
            del self.snap_vol[sid]
        for bid in [b for b, r in self.bkp_repo.items() if r == vid]:
            del self.bkp_repo[bid]

    # ------------------------------------------------------------------ one call
    def observe(self, twin: StorageTwin, op: str, args: dict[str, Any], res: dict[str, Any], file_sb: Sandbox,
                file_loc: dict[str, str]) -> None:  # fmt: skip
        self.step += 1
        ok = bool(res.get("ok"))
        try:
            rec = self._accepted(twin, op, args, res, file_sb, file_loc) if ok else self._refused(twin, op, args)
        except (KeyError, RuntimeError) as exc:
            rec = CallRecord(self.step, op, ok, "n/a", f"driver error: {exc!r}")
            self.errors.append(f"{op}: {exc!r}")
        self.calls.append(rec)
        self.check(twin, op)

    def _refused(self, twin: StorageTwin, op: str, args: dict[str, Any]) -> CallRecord:
        """The twin refused: try the native command under a checkpoint, then rewind."""
        if op not in DESTRUCTIVE_REFUSABLE:
            return CallRecord(self.step, op, False, "not-modelled", "refusal reason has no ZFS counterpart")
        item: dict[str, Any]
        if op == "delete_volume":
            vid = args.get("volume_id")
            if vid not in self.where:
                return CallRecord(self.step, op, False, "n/a", "no such volume")
            if args.get("to_recycle_bin"):
                return CallRecord(self.step, op, False, "emulated", "recycle bin is emulated (zfs rename always succeeds)")
            item = {"op": "destroy", "target": self.ds(vid), "flags": "R" if args.get("force") else ""}
        elif op == "purge_recycle_bin":
            vid = args.get("volume_id")
            if vid is None or vid not in self.where:
                return CallRecord(self.step, op, False, "n/a", "no such volume")
            if self.where[vid][1] != "recycle-bin":
                return CallRecord(self.step, op, False, "emulated", "not in the (emulated) recycle bin")
            item = {"op": "destroy", "target": self.ds(vid), "flags": "R"}
        elif op == "delete_snapshot":
            sid = args.get("snapshot_id")
            if sid not in self.snap_vol:
                return CallRecord(self.step, op, False, "n/a", "no such snapshot")
            item = {"op": "destroy", "target": self.snap(sid), "flags": "R" if args.get("force") else ""}
        elif op == "restore_snapshot":
            vid, sid = args.get("volume_id"), args.get("snapshot_id")
            if vid not in self.where or sid not in self.snap_vol:
                return CallRecord(self.step, op, False, "n/a", "no such volume or snapshot")
            item = {"op": "rollback", "snap": f"{self.ds(vid)}@{sid}"}  # a snapshot of another volume: ZFS refuses too
        else:  # delete_backup
            bid = args.get("backup_id")
            if bid not in self.bkp_repo:
                return CallRecord(self.step, op, False, "n/a", "no such backup")
            item = {"op": "unlink", "path": self.bkp(bid)}
        if self.step in self.memo:  # observed on an earlier pass, which was then rewound
            return self.memo[self.step]
        before = self.actual()
        r = self.a(item["op"], **{k: v for k, v in item.items() if k != "op"})
        rec = CallRecord(self.step, op, False, "ok" if r["ok"] else "refused", (r.get("out") or "")[:300],
                         r.get("destroyed", []) if r["ok"] else [])  # fmt: skip
        if self.actual() != before:  # ZFS did something the twin refused: undo it by rewinding, then replay
            rec.followed = "rewound to the checkpoint and replayed"
            raise ZfsRestart(self.step, rec)
        return rec

    def _pool_for(self, item: dict[str, Any]) -> str:
        if "target" in item:
            return item["target"].split("/")[0].split("@")[0]
        if "snap" in item:
            return item["snap"].split("/")[0]
        rel = item["path"]
        mnt = "/".join(rel.split("/")[:2])
        return next(p for p, m in self.mnt_of.items() if m == mnt)

    def _accepted(self, twin: StorageTwin, op: str, args: dict[str, Any], res: dict[str, Any], file_sb: Sandbox,
                  file_loc: dict[str, str]) -> CallRecord:  # noqa: C901 - one branch per tool  # fmt: skip
        st = twin.state
        rec = CallRecord(self.step, op, True, "ok")
        if op in NO_DATA_EFFECT:
            rec.native = "no-data-effect"
            return rec
        if op == "create_volume":
            vid = res["volume"]["id"]
            self.where[vid] = self._where(twin, vid)
            r = self.a("create", ds=self.ds(vid))
            self.a("write", path=self.img(vid), text=file_sb.read(image(file_loc[vid])))
            rec.native, rec.detail = ("ok", "") if r["ok"] else ("refused", r["out"])
        elif op in ("delete_volume", "purge_recycle_bin"):
            if res.get("in_recycle_bin"):
                vid = res["volume_id"]
                old = self.ds(vid)
                self.where[vid] = self._where(twin, vid)
                r = self.a("rename", src=old, dst=self.ds(vid))
                rec.native, rec.detail = ("emulated", "recycle bin = zfs rename") if r["ok"] else ("refused", r["out"])
                return rec
            gone = [res["volume_id"]] if op == "delete_volume" else list(res.get("purged", []))
            flags = "R" if (op == "purge_recycle_bin" or args.get("force")) else ""
            rec.twin_destroyed = self._twin_cascade(gone + list(res.get("deleted_clones", [])), res)
            for vid in gone:
                r = self._destroy(self.ds(vid), flags)
                rec.destroyed += r.get("destroyed", []) if r["ok"] else []
                if not r["ok"]:
                    rec.native, rec.detail = "refused", r["out"][:300]
                    rec.followed = self._force_destroy(self.ds(vid))
            for vid in gone + list(res.get("deleted_clones", [])):
                self._forget(vid)
        elif op in ("restore_from_recycle_bin", "migrate_volume"):
            vid = res["volume_id"]
            old = self.ds(vid)
            self.where[vid] = self._where(twin, vid)
            r = self.a("rename", src=old, dst=self.ds(vid))
            rec.native = "emulated" if op == "restore_from_recycle_bin" else "ok"
            if not r["ok"]:
                rec.native, rec.detail = "refused", r["out"]
        elif op == "create_snapshot":
            for s in res["snapshots"]:
                self.snap_vol[s["id"]] = s["volume_id"]
            r = self.a("snapshot", snaps=[self.snap(s["id"]) for s in res["snapshots"]])  # atomic, like a group snapshot
            held = [self.snap(s["id"]) for s in res["snapshots"] if s["immutable"]]
            if held:
                self.a("hold", snaps=held)
            rec.native, rec.detail = ("ok", "") if r["ok"] else ("refused", r["out"])
        elif op == "delete_snapshot":
            sid = res["snapshot_id"]
            rec.twin_destroyed = sorted({self.snap(sid), *self._twin_cascade(list(res.get("deleted_clones", [])), res)})
            r = self._destroy(self.snap(sid), "R" if args.get("force") else "")
            rec.destroyed = r.get("destroyed", []) if r["ok"] else []
            if not r["ok"]:
                rec.native, rec.detail = "refused", r["out"][:300]
                rec.followed = self._force_destroy(self.snap(sid))
            for vid in res.get("deleted_clones", []):
                self._forget(vid)
            self.snap_vol.pop(sid, None)
        elif op == "restore_snapshot":
            refusals = []
            for p in res["restored"]:
                vid, sid = p["volume_id"], p["snapshot_id"]
                r = self.a("rollback", snap=self.snap(sid))
                if not r["ok"]:
                    refusals.append(r["out"].splitlines()[0] if r["out"] else "refused")
                    rec.would_destroy += [ln.strip() for ln in r["out"].splitlines()[1:] if "@" in ln and " " not in ln.strip()]
                    text = self.a("read", paths=[self.snap_img(sid)])["texts"][self.snap_img(sid)]
                    self.a("write", path=self.img(vid), text=text)
            for p in res["restored"]:
                for link in self._active_links_from(twin, p["volume_id"]):
                    self.ship(link["source_volume_id"], link["target_volume_id"])
            if refusals:
                rec.native, rec.detail = "refused", refusals[0][:300]
                rec.followed = "copied the snapshot's data.img into the volume (newer snapshots kept, as in the twin)"
        elif op == "create_clone":
            v = res["volume"]
            vid = v["id"]
            self.where[vid] = self._where(twin, vid)
            r = self.a("clone", snap=self.snap(v["parent_snapshot_id"]), ds=self.ds(vid))
            self.a("write", path=self.img(vid), text=file_sb.read(image(file_loc[vid])))
            rec.native, rec.detail = ("ok", "") if r["ok"] else ("refused", r["out"])
        elif op == "set_snapshot_immutable":
            snap = self.snap(res["snapshot_id"])
            inv = {it["name"]: it for it in self.a("inventory")["items"]}
            held = inv.get(snap, {}).get("userrefs", "0") not in ("0", "-")
            if res["immutable"] and not held:
                r = self.a("hold", snaps=[snap])
            elif not res["immutable"] and held:
                r = self.a("release", snaps=[snap])
            else:
                r = {"ok": True}
                rec.detail = "already in that state"
            if not r["ok"]:
                rec.native, rec.detail = "refused", r["out"]
        elif op == "create_replication":
            link = res["replication_link"]
            src, tid = link["source_volume_id"], link["target_volume_id"]
            self.where[tid] = self._where(twin, tid)
            tmp = f"{self.ds(src)}@sgrepl"
            self.a("snapshot", snaps=[tmp])
            r = self.a("send_recv", snap=tmp, ds=self.ds(tid), force=False)
            self._destroy(tmp, "")
            if r["ok"]:
                self._destroy(f"{self.ds(tid)}@sgrepl", "")
            else:
                rec.native, rec.detail = "refused", r["out"]
        elif op == "resume_replication":
            source = to_public("replication_links", st.replication_links[res["link_id"]])["source_volume_id"]
            hows = [self.ship(link["source_volume_id"], link["target_volume_id"]) for link in self._active_links_from(twin, source)]
            if any(h != "send|recv -F" for h in hows):
                rec.native, rec.detail = "refused", next(h for h in hows if h != "send|recv -F")
                rec.followed = "copied data.img"
        elif op == "run_backup":
            b = res["backup"]
            self.bkp_repo[b["id"]] = b["repository"]
            tmp = f"{self.ds(b['source_volume_id'])}@sgbk"
            self.a("snapshot", snaps=[tmp])
            r = self.a("send_file", snap=tmp, path=self.bkp(b["id"]))
            self._destroy(tmp, "")
            rec.native, rec.detail = ("ok", "") if r["ok"] else ("refused", r["out"])
        elif op == "delete_backup":
            bid = res["backup_id"]
            r = self.a("unlink", path=self.bkp(bid))
            if not r["ok"]:
                rec.native, rec.detail = "refused", r["out"]
                self.a("chattr", path=self.bkp(bid), immutable=False)
                self.a("unlink", path=self.bkp(bid))
                rec.followed = "cleared the immutable flag and removed it"
            rec.destroyed = [self.bkp(bid)]
            del self.bkp_repo[bid]
        else:
            rec.native, rec.detail = "n/a", "no ZFS handler"
            self.errors.append(f"{op}: no ZFS handler")
        return rec

    # ------------------------------------------------------------------ read everything back
    def export(self, twin: StorageTwin, base_dir: str | None, restored: dict[str, str] | None = None) -> tuple[Sandbox, dict]:
        """Read every object back from ZFS into a fresh file sandbox laid out like the file run.

        Backups are restored with ``zfs receive`` (a stream whose bytes were already restored in
        this session, per ``restored`` keyed by sha256, is not received twice).
        """
        st = twin.state
        restored = {} if restored is None else restored
        loc = {vid: volume_dir(st, vid) for vid in st.volumes}
        reads: dict[str, str] = {}
        for vid in st.volumes:
            reads[self.img(vid)] = image(loc[vid])
        for sid in st.snapshots:
            reads[self.snap_img(sid)] = snapshot_file(loc[self.snap_vol[sid]], sid)
        texts = self.a("read", paths=list(reads))["texts"]
        bk = {bid: self.bkp(bid) for bid in st.backups if bid in self.bkp_repo}
        shas = self.a("sha", paths=list(bk.values()))["sha"] if bk else {}
        scratch = self.scratch_pool
        smnt = self.mnt_of[scratch]
        received = 0
        for bid, path in sorted(bk.items()):
            sha = shas.get(path)
            if sha is None:
                continue
            if sha not in restored:
                r = self.a("recv_file", path=path, ds=f"{scratch}/r")
                got = self.a("read", paths=[f"{smnt}/r/data.img"])["texts"][f"{smnt}/r/data.img"] if r["ok"] else None
                self._destroy(f"{scratch}/r", "r")
                received += 1
                if got is None:
                    self.errors.append(f"backup {bid}: zfs receive failed: {r.get('out')}")
                    continue
                restored[sha] = got
            texts[path] = restored[sha]
            reads[path] = backup_file(self.bkp_repo[bid], bid, loc)
        inv = self.a("inventory")["items"]
        held = {it["name"] for it in inv if it["type"] == "snapshot" and it.get("userrefs", "0") not in ("0", "-")}
        imm = set(self.a("files")["immutable"])
        sb = Sandbox.create(base_dir)
        missing = []
        for zpath, rel in reads.items():
            if texts.get(zpath) is None:
                missing.append(zpath)
                continue
            sb.write(rel, texts[zpath])
        for sid in st.snapshots:
            if self.snap(sid) in held and sb.exists(reads[self.snap_img(sid)]):
                sb.lock(reads[self.snap_img(sid)])
        for path in bk.values():
            if path in imm and path in reads and sb.exists(reads[path]):
                sb.lock(reads[path])
        return sb, {"files": len(reads), "missing": missing[:10], "received": received}


def compare_sandboxes(a: Sandbox, b: Sandbox) -> dict[str, Any]:
    fa, fb = set(a.files()), set(b.files())
    differ = sorted(f for f in fa & fb if a.read(f) != b.read(f))
    out = {"only_file": sorted(fa - fb)[:10], "only_zfs": sorted(fb - fa)[:10], "differ": differ[:10],
           "locks_only_file": sorted(a.locks - b.locks)[:10], "locks_only_zfs": sorted(b.locks - a.locks)[:10]}  # fmt: skip
    out["identical"] = not any(out.values())
    out["files"] = len(fa)
    return out


class ZfsTwin(PhysicalTwin):
    """A twin whose executed mutations happen to real files *and* to OpenZFS."""

    zfs: ZfsExecutor | None = None

    def execute(self, op: str, args: dict[str, Any] | None = None, actor: str = "agent") -> dict[str, Any]:
        res = super().execute(op, args, actor)
        spec = OPS.get(op)
        if self.zfs is not None and spec is not None and spec.mutating and self.executor is not None:
            self.zfs.observe(self, op, dict(args or {}), res, self.executor.sb, self.executor.loc)
        return res


class ZfsWorld:
    """A task's starting world on OpenZFS: built once, verified once, then rewound per trajectory.

    After materialisation every object is read back (backups through ``zfs receive``) and
    compared byte for byte with the file sandbox; then each array pool and the vault pool get a
    ``zpool checkpoint``.  ``rewind()`` (export + ``import --rewind-to-checkpoint``) returns
    them exactly to that verified state for the next trajectory of the same task.
    """

    def __init__(self, agent: Agent, twin: StorageTwin, base_dir: str | None = None) -> None:
        t0 = time.perf_counter()
        self.agent, self.base_dir = agent, base_dir
        sb = Sandbox.create(base_dir)
        try:
            from .world import materialise

            manifest = materialise(twin.state, sb)
            zx = ZfsExecutor(agent, twin)
            zx.materialise(twin, sb)
            self.pools = zx.pools()
            self.restored: dict[str, str] = {}  # sha256 of a backup stream -> the data.img a zfs receive gave
            zsb, info = zx.export(twin, base_dir, self.restored)
            try:
                self.start_cmp = compare_sandboxes(sb, zsb) | {"export": info}
                self.start_rb = read_back(zsb)
                self.start_heads = {v: _head(zsb, image(d)) for v, d in manifest.loc.items()}
            finally:
                zsb.destroy()
            zx.check(twin, "start")
            self.start_drift = zx.drift
        finally:
            sb.destroy()
        self.checkpointed = [*self.pools["pool_of"].values(), self.pools["vault_pool"]]
        for pool in self.checkpointed:
            r = agent("checkpoint", pool=pool)
            if not r["ok"]:
                raise RuntimeError(f"zpool checkpoint {pool}: {r['out']}")
        self.rewinds = 0
        self.t_build = time.perf_counter() - t0

    def rewind(self) -> None:
        for pool in self.checkpointed:
            for op in ("rewind", "checkpoint"):
                r = self.agent(op, pool=pool)
                if not r["ok"]:
                    raise RuntimeError(f"{op} {pool}: {r['out']}")
        self.rewinds += 1

    def close(self) -> None:
        r = self.agent("reset")
        if not r["ok"]:
            raise RuntimeError(f"ZFS reset failed: {r['errors']}")


class ZfsSession:
    """One trajectory on both the file sandbox and OpenZFS; measured on each, then compared."""

    def __init__(self, twin: StorageTwin, world: ZfsWorld, memo: dict[int, CallRecord] | None = None) -> None:
        t0 = time.perf_counter()
        self.world, self.base_dir = world, world.base_dir
        self.file = PhysicalSession(twin, world.base_dir)
        ft = self.file.twin
        zt = ZfsTwin(ft.state, ft.oplog)
        zt.executor = ft.executor
        self.file.twin = zt
        self.twin = zt
        self.zx = ZfsExecutor(world.agent, zt, world.pools, memo)
        self.zx.check(zt, "start")  # the rewound pools hold exactly the starting inventory
        zt.zfs = self.zx
        self.t_setup = time.perf_counter() - t0

    def finish(self, allowed: Any, base: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        w, zt, ex = self.world, self.twin, self.twin.executor
        assert ex is not None
        zsb, info = self.zx.export(zt, self.base_dir, w.restored)
        try:
            end_cmp = compare_sandboxes(self.file.sb, zsb) | {"export": info}
            file_out = self.file.finish(allowed, base)
            zfs_out = measure_end(zsb, ex.loc, self.file.manifest, self.file.start, w.start_rb, w.start_heads,
                                  capture_public(zt), allowed, base, zt.state.now, self.file.now0, self.base_dir)  # fmt: skip
        finally:
            zsb.destroy()
        return {
            "file": file_out, "zfs": zfs_out, "calls": [c.as_dict() for c in self.zx.calls], "start": w.start_cmp,
            "end": end_cmp, "drift": self.zx.drift[:5], "n_drift": len(self.zx.drift), "errors": self.zx.errors[:5],
            "seconds": {"setup": round(self.t_setup, 2), "finish": round(time.perf_counter() - t0, 2)},
        }  # fmt: skip

    def close(self) -> None:
        self.file.close()


def run_trajectory(world: ZfsWorld, twin_factory: Any, calls: list[tuple[str, dict]], allowed: Any,
                   base: Any) -> dict[str, Any]:  # fmt: skip
    """Run one trajectory from the world's checkpoint; rewind afterwards (and in between, see ZfsRestart)."""
    memo: dict[int, CallRecord] = {}
    passes = 0
    while True:
        passes += 1
        s = ZfsSession(twin_factory(), world, memo)
        try:
            for tool, args in calls:
                s.twin.execute(tool, args)
            out = s.finish(allowed, base)
        except ZfsRestart as r:
            memo[r.step] = r.record
            continue
        finally:
            s.close()
            world.rewind()
        out["passes"] = passes
        return out
