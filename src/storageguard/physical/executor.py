"""The storage driver: executes every successful mutation on the sandbox's real files.

``PhysicalTwin`` is a :class:`StorageTwin` whose successful mutations are also carried
out by a :class:`PhysicalExecutor`.  The control plane (the twin) decides *what* an
operation does; the driver does it to the bytes: removing a volume's directory removes
its snapshots and the backups stored in it, a restore overwrites the image, a lock sets
the immutable flag.  The driver reads only public fields and operation results (never
the hidden lineage fields), and after every mutation it checks that the files on disk
match the control plane's inventory; any mismatch is recorded as *drift*.

Dry runs (guard, judge probe, oracle) use ``twin.clone()``, which is a plain twin, so
only calls that the middleware actually executes ever reach the files.
"""

from __future__ import annotations

from typing import Any

from ..twin.models import OFFSITE_VAULT
from ..twin.operations import OPS
from ..twin.public import parse_iso, to_public
from ..twin.state import StorageTwin
from .image import body, commit_line, parse, render
from .layout import backup_file, image, snapshot_file, volume_dir
from .sandbox import Sandbox

NO_DATA_EFFECT = frozenset({
    "resize_volume", "set_qos", "map_volume", "unmap_volume", "create_zone", "add_zone_member",
    "remove_zone_member", "delete_zone", "pause_replication", "break_replication", "add_hot_spare",
    "remove_hot_spare",
})  # fmt: skip


def new_lineage(volume_id: str) -> str:
    return f"new-{volume_id}"


class PhysicalExecutor:
    def __init__(self, sb: Sandbox, twin: StorageTwin) -> None:
        self.sb = sb
        st = twin.state
        self.loc: dict[str, str] = {vid: volume_dir(st, vid) for vid in st.volumes}
        self.snap_vol: dict[str, str] = {sid: to_public("snapshots", s)["volume_id"] for sid, s in st.snapshots.items()}
        self.bkp_repo: dict[str, str] = {bid: to_public("backups", b)["repository"] for bid, b in st.backups.items()}
        self.deletions: list[dict[str, Any]] = []  # every file really removed or overwritten
        self.drift: list[dict[str, Any]] = []
        self.applied = 0
        self.errors: list[str] = []

    # ------------------------------------------------------------------ paths
    def image_of(self, vid: str) -> str:
        return image(self.loc[vid])

    def snapshot_path(self, sid: str) -> str:
        return snapshot_file(self.loc[self.snap_vol[sid]], sid)

    def backup_path(self, bid: str) -> str:
        return backup_file(self.bkp_repo[bid], bid, self.loc)

    # ------------------------------------------------------------------ logging
    def _record(self, op: str, action: str, files: list[tuple[str, int]], texts: dict[str, str]) -> None:
        """Log what was really destroyed: each file, its size, and the readable data it held."""
        self.deletions.append({"op": op, "action": action, "files": [
            {"path": f, "bytes": n, "held": parse(texts.get(f, "")).newest} for f, n in files]})  # fmt: skip

    def _rmtree(self, op: str, rel: str) -> None:
        texts = {f: self.sb.read(f) for f in self.sb.files(rel)}
        self._record(op, "rmtree", self.sb.rmtree(rel), texts)

    def _unlink(self, op: str, rel: str) -> None:
        text = self.sb.read(rel)
        self._record(op, "unlink", [(rel, self.sb.unlink(rel))], {rel: text})

    def _overwrite(self, op: str, rel: str, text: str) -> None:
        old = self.sb.read(rel) if self.sb.exists(rel) else ""
        if old and old != text:
            self._record(op, "overwrite", [(rel, len(old.encode()))], {rel: old})
        self.sb.write(rel, text)

    def ship(self, op: str, link: dict[str, Any]) -> None:
        """Replication: the target becomes a byte copy of the source."""
        src, dst = self.image_of(link["source_volume_id"]), self.image_of(link["target_volume_id"])
        self._overwrite(op, dst, self.sb.read(src))

    def _forget_volume(self, vid: str) -> None:
        self.loc.pop(vid, None)
        for sid in [s for s, v in self.snap_vol.items() if v == vid]:
            del self.snap_vol[sid]
        for bid in [b for b, r in self.bkp_repo.items() if r == vid]:
            del self.bkp_repo[bid]

    # ------------------------------------------------------------------ the driver
    def apply(self, twin: StorageTwin, op: str, res: dict[str, Any]) -> None:
        """Carry out one successful mutation (``res`` is the tool result the agent saw)."""
        self.applied += 1
        try:
            self._apply(twin, op, res)
        except (OSError, KeyError) as exc:  # a data-plane failure is a fidelity finding, not a crash
            self.errors.append(f"{op}: {exc!r}")
        self.check(twin, op)

    def _active_links_from(self, twin: StorageTwin, vid: str) -> list[dict[str, Any]]:
        links = [to_public("replication_links", x) for x in twin.state.replication_links.values()]
        return [x for x in links if x["source_volume_id"] == vid and x["state"] == "active"]

    def _apply(self, twin: StorageTwin, op: str, res: dict[str, Any]) -> None:  # noqa: C901 - one branch per tool
        st = twin.state
        if op in NO_DATA_EFFECT:
            return
        if op == "create_volume":
            vid = res["volume"]["id"]
            self.loc[vid] = volume_dir(st, vid)
            self.sb.write(self.image_of(vid), render([commit_line(new_lineage(vid), parse_iso(res["volume"]["created_at"]))]))
        elif op in ("delete_volume", "purge_recycle_bin"):
            if res.get("in_recycle_bin"):
                vid = res["volume_id"]
                new = volume_dir(st, vid)
                self.sb.move(self.loc[vid], new)
                self.loc[vid] = new
                return
            gone = [res["volume_id"]] if op == "delete_volume" else list(res.get("purged", []))
            for vid in gone + list(res.get("deleted_clones", [])):
                if vid in self.loc:
                    self._rmtree(op, self.loc[vid])
                    self._forget_volume(vid)
        elif op in ("restore_from_recycle_bin", "migrate_volume"):
            vid = res["volume_id"]
            new = volume_dir(st, vid)
            self.sb.move(self.loc[vid], new)
            self.loc[vid] = new
        elif op == "create_snapshot":
            for s in res["snapshots"]:
                self.snap_vol[s["id"]] = s["volume_id"]
                rel = self.snapshot_path(s["id"])
                self.sb.copy_file(self.image_of(s["volume_id"]), rel)
                if s["immutable"]:
                    self.sb.lock(rel)
        elif op == "delete_snapshot":
            sid = res["snapshot_id"]
            for vid in res.get("deleted_clones", []):
                if vid in self.loc:
                    self._rmtree(op, self.loc[vid])
                    self._forget_volume(vid)
            self._unlink(op, self.snapshot_path(sid))
            del self.snap_vol[sid]
        elif op == "restore_snapshot":
            restored = [(p["volume_id"], p["snapshot_id"]) for p in res["restored"]]
            texts = [self.sb.read(self.snapshot_path(sid)) for _, sid in restored]  # read all before writing any
            for (vid, _), text in zip(restored, texts, strict=True):
                self._overwrite(op, self.image_of(vid), text)
            for vid, _ in restored:  # active replication mirrors the restored contents (corruption included)
                for link in self._active_links_from(twin, vid):
                    self.ship(op, link)
        elif op == "create_clone":
            v = res["volume"]
            vid = v["id"]
            self.loc[vid] = volume_dir(st, vid)
            parent = self.sb.read(self.snapshot_path(v["parent_snapshot_id"]))
            lines = body(parent) + [commit_line(new_lineage(vid), parse_iso(v["created_at"]))]
            self.sb.write(self.image_of(vid), render(lines))
        elif op == "set_snapshot_immutable":
            rel = self.snapshot_path(res["snapshot_id"])
            (self.sb.lock if res["immutable"] else self.sb.unlock)(rel)
        elif op == "create_replication":
            link = res["replication_link"]
            tid = link["target_volume_id"]
            self.loc[tid] = volume_dir(st, tid)
            self.sb.copy_file(self.image_of(link["source_volume_id"]), self.image_of(tid))
        elif op == "resume_replication":  # the source's active links resynchronise
            source = to_public("replication_links", st.replication_links[res["link_id"]])["source_volume_id"]
            for link in self._active_links_from(twin, source):
                self.ship(op, link)
        elif op == "run_backup":
            b = res["backup"]
            self.bkp_repo[b["id"]] = b["repository"]
            self.sb.copy_file(self.image_of(b["source_volume_id"]), self.backup_path(b["id"]))
        elif op == "delete_backup":
            bid = res["backup_id"]
            self._unlink(op, self.backup_path(bid))
            del self.bkp_repo[bid]
        else:
            self.errors.append(f"{op}: no physical handler")

    # ------------------------------------------------------------------ fidelity check
    def expected_files(self, twin: StorageTwin) -> set[str]:
        st = twin.state
        out = {image(volume_dir(st, vid)) for vid in st.volumes}
        for s in st.snapshots.values():
            out.add(snapshot_file(volume_dir(st, to_public("snapshots", s)["volume_id"]), s.id))
        for b in st.backups.values():
            repo = to_public("backups", b)["repository"]
            if repo == OFFSITE_VAULT or repo in st.volumes:
                out.add(backup_file(repo, b.id, {r: volume_dir(st, r) for r in st.volumes if r == repo}))
        return out

    def check(self, twin: StorageTwin, op: str) -> None:
        """Files on disk == the control plane's inventory, and locks == immutable flags."""
        want, have = self.expected_files(twin), set(self.sb.files())
        locked_want = {snapshot_file(volume_dir(twin.state, s.volume_id), s.id)
                       for s in twin.state.snapshots.values() if to_public("snapshots", s)["immutable"]}  # fmt: skip
        locked_want |= {self.backup_path(b) for b, x in twin.state.backups.items()
                        if to_public("backups", x)["immutable"] and b in self.bkp_repo}  # fmt: skip
        if want != have or locked_want != self.sb.locks:
            self.drift.append({"op": op, "missing": sorted(want - have), "extra": sorted(have - want),
                               "lock_mismatch": sorted(locked_want ^ self.sb.locks)})  # fmt: skip


class PhysicalTwin(StorageTwin):
    """A twin whose executed mutations also happen to real files."""

    executor: PhysicalExecutor | None = None

    @classmethod
    def attach(cls, twin: StorageTwin, sb: Sandbox) -> PhysicalTwin:
        pt = cls(twin.state, twin.oplog)
        pt.executor = PhysicalExecutor(sb, pt)
        return pt

    def execute(self, op: str, args: dict[str, Any] | None = None, actor: str = "agent") -> dict[str, Any]:
        res = super().execute(op, args, actor)
        spec = OPS.get(op)
        if self.executor is not None and res.get("ok") and spec is not None and spec.mutating:
            self.executor.apply(self, op, res)
        return res
