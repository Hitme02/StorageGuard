"""StorageGuard ZFS agent: runs INSIDE the disposable Lima VM, never on the host.

The host starts it with ``limactl shell sg-zfs -- sudo python3 -c <this file>`` and talks to it
in JSON lines over stdin/stdout.  It is stdlib-only and self-contained (it is shipped as text).

Safety rules (refusal is the default):

* it refuses to start unless the VM shows no host or network file system: no virtiofs, 9p,
  sshfs, NFS, CIFS or FUSE mount, no ``/Users`` or ``/Volumes``, and nothing mounted under
  ``/mnt`` except Lima's read-only cloud-init ISO;
* every pool is created by this agent on a sparse *file* under ``/var/lib/sgzfs/s-<token>/vdev``
  on the VM's own disk: no block device is ever named, and no other pool is ever touched;
* every pool is named ``sgz<token>_<name>`` and tagged ``org.storageguard:session=<token>``,
  and every dataset, snapshot and path argument is checked to lie inside this session;
* the operations form a closed set (no shell, no free-form command): create, destroy, snapshot,
  hold, release, rollback, clone, rename, send/receive, and file reads and writes;
* teardown destroys only pools carrying this session's tag, then this session's directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys

BASE = "/var/lib/sgzfs"
VDEV_BYTES = 2 * 1024**3  # sparse
FORBIDDEN_FS = {"virtiofs", "9p", "sshfs", "fuse.sshfs", "nfs", "nfs4", "cifs", "smb3", "smbfs", "vboxsf", "davfs"}
CIDATA = ("/mnt/lima-cidata", "iso9660")  # Lima's read-only cloud-init disk (a VM block device, not a share)
LOCAL_FS = {"ext4", "xfs", "btrfs"}
NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
SNAP = r"[A-Za-z0-9][A-Za-z0-9_.:-]*"
HOLD_TAG = "sg-lock"


class Refused(Exception):
    """A request outside the sandbox; nothing was done."""


def fatal(msg: str) -> None:
    print(json.dumps({"fatal": msg}), flush=True)
    sys.exit(2)


def mounts() -> list[tuple[str, str, str, str]]:
    with open("/proc/mounts") as f:
        return [tuple(line.split()[:4]) for line in f if line.strip()]  # type: ignore[misc]


def mount_problems(table: list[tuple[str, str, str, str]]) -> list[str]:
    """Every mount that could expose the host or a network share (an empty list means safe)."""
    out = []
    for _dev, mnt, fstype, opts in table:
        if fstype in FORBIDDEN_FS or fstype in ("fuse", "fuseblk") or fstype.startswith("fuse."):  # not fusectl
            out.append(f"host or network file system mounted at {mnt} ({fstype})")
        elif mnt.startswith(("/Users", "/Volumes", "/private", "/home/")) and fstype not in LOCAL_FS:
            out.append(f"unexpected mount {mnt} ({fstype})")
        elif mnt.startswith("/mnt") and (mnt, fstype) != CIDATA:
            out.append(f"unexpected mount {mnt} ({fstype})")
        elif (mnt, fstype) == CIDATA and "ro" not in opts.split(","):
            out.append("the cloud-init disk is not read-only")
    return out


def preflight() -> None:
    if os.geteuid() != 0:
        fatal("the agent must run as root inside the VM")
    for p in ("/Users", "/Volumes", "/private"):
        if os.path.exists(p):
            fatal(f"{p} exists: this looks like a host file system is visible; refusing")
    problems = mount_problems(mounts())
    if problems:
        fatal("; ".join(problems) + "; refusing")
    os.makedirs(BASE, mode=0o700, exist_ok=True)
    best = max((m for m in mounts() if BASE.startswith(m[1].rstrip("/") + "/") or m[1] == "/"), key=lambda m: len(m[1]))
    if best[2] not in LOCAL_FS or not best[0].startswith("/dev/vd"):
        fatal(f"{BASE} is not on the VM's own disk ({best[0]} {best[2]}); refusing")


def run(argv: list[str], *, stdin=None, stdout=None) -> tuple[int, str]:
    p = subprocess.run(argv, stdin=stdin, stdout=stdout if stdout is not None else subprocess.PIPE,
                       stderr=subprocess.PIPE, text=stdout is None, check=False)  # fmt: skip
    out = p.stdout if stdout is None else ""
    err = p.stderr if isinstance(p.stderr, str) else p.stderr.decode(errors="replace")
    return p.returncode, ((out or "") + (err or "")).strip()


class Session:
    def __init__(self) -> None:
        self.token = secrets.token_hex(4)
        self.root = f"{BASE}/s-{self.token}"
        os.makedirs(f"{self.root}/vdev", mode=0o700)
        os.makedirs(f"{self.root}/mnt", mode=0o700)
        self.pools: list[str] = []
        self.pool_re = re.compile(rf"sgz{self.token}_[a-z0-9][a-z0-9-]*")
        self.ds_re = re.compile(rf"({self.pool_re.pattern})(/{NAME})*")
        self.snap_re = re.compile(rf"({self.ds_re.pattern})@{SNAP}")

    # ------------------------------------------------------------------ validation
    def pool(self, name: str) -> str:
        if not isinstance(name, str) or not self.pool_re.fullmatch(name) or name not in self.pools:
            raise Refused(f"not a pool of this session: {name!r}")
        return name

    def ds(self, name: str) -> str:
        if not isinstance(name, str) or not self.ds_re.fullmatch(name) or name.split("/")[0] not in self.pools:
            raise Refused(f"not a dataset of this session: {name!r}")
        return name

    def snap(self, name: str) -> str:
        if not isinstance(name, str) or not self.snap_re.fullmatch(name) or name.split("/")[0] not in self.pools:
            raise Refused(f"not a snapshot of this session: {name!r}")
        return name

    def path(self, rel: str, *, allow_snapdir: bool = False) -> str:
        """A path under <root>/mnt; no '..', no absolute path, no symlink anywhere on the way."""
        if not isinstance(rel, str) or not rel.startswith("mnt/") or rel.startswith("/") or "\x00" in rel:
            raise Refused(f"path outside the session: {rel!r}")
        parts = rel.split("/")
        if any(p in ("", ".", "..") for p in parts) or (".zfs" in parts and not allow_snapdir):
            raise Refused(f"path outside the session: {rel!r}")
        full = os.path.join(self.root, rel)
        cur = self.root
        for p in parts:
            cur = os.path.join(cur, p)
            if os.path.islink(cur):
                raise Refused(f"symlink in path: {rel!r}")
        if os.path.commonpath([os.path.realpath(full), self.root]) != self.root:
            raise Refused(f"path escapes the session: {rel!r}")
        return full

    # ------------------------------------------------------------------ ops
    def zfs(self, *argv: str) -> dict:
        rc, out = run(["zfs", *argv])
        return {"ok": rc == 0, "rc": rc, "out": out[-2000:]}

    def op_pool_create(self, r: dict) -> dict:
        name = r["name"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name or ""):
            raise Refused(f"bad pool name {name!r}")
        pool = f"sgz{self.token}_{name}"
        if pool in self.pools:
            raise Refused(f"pool exists: {pool}")
        vdev = f"{self.root}/vdev/{name}.img"
        with open(vdev, "wb") as f:
            f.truncate(VDEV_BYTES)
        mnt = f"{self.root}/mnt/{name}"
        rc, out = run(["zpool", "create", "-o", "ashift=12", "-O", "compression=off", "-O", "atime=off",
                       "-O", f"mountpoint={mnt}", "-O", f"org.storageguard:session={self.token}", pool, vdev])  # fmt: skip
        if rc != 0:
            os.unlink(vdev)
            return {"ok": False, "out": out}
        self.pools.append(pool)
        return {"ok": True, "pool": pool, "mnt": f"mnt/{name}"}

    def op_create(self, r: dict) -> dict:
        return self.zfs("create", *(["-p"] if r.get("parents") else []), self.ds(r["ds"]))

    def op_destroy(self, r: dict) -> dict:
        target = r["target"]
        (self.snap if "@" in target else self.ds)(target)
        if "/" not in target.split("@")[0]:
            raise Refused("refusing to destroy a pool's root dataset")
        flags = r.get("flags", "")
        if flags not in ("", "r", "R"):
            raise Refused(f"bad destroy flags {flags!r}")
        argv = ["destroy", "-v", *(["-n"] if r.get("dry") else []), *([f"-{flags}"] if flags else []), target]
        res = self.zfs(*argv)
        res["destroyed"] = [ln.split()[-1] for ln in res["out"].splitlines() if ln.startswith(("will destroy ", "would destroy "))]
        return res

    def op_snapshot(self, r: dict) -> dict:
        return self.zfs("snapshot", *[self.snap(s) for s in r["snaps"]])

    def op_hold(self, r: dict) -> dict:
        return self.zfs("hold", HOLD_TAG, *[self.snap(s) for s in r["snaps"]])

    def op_release(self, r: dict) -> dict:
        return self.zfs("release", HOLD_TAG, *[self.snap(s) for s in r["snaps"]])

    def op_rollback(self, r: dict) -> dict:  # never -r/-R/-f: a native rollback that would destroy anything is refused
        return self.zfs("rollback", self.snap(r["snap"]))

    def op_clone(self, r: dict) -> dict:
        return self.zfs("clone", self.snap(r["snap"]), self.ds(r["ds"]))

    def op_rename(self, r: dict) -> dict:
        src, dst = self.ds(r["src"]), self.ds(r["dst"])
        if src.split("/")[0] != dst.split("/")[0]:
            raise Refused("rename across pools")
        return self.zfs("rename", src, dst)

    def op_send_file(self, r: dict) -> dict:
        snap, path = self.snap(r["snap"]), self.path(r["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            rc, out = run(["zfs", "send", snap], stdout=f)
        return {"ok": rc == 0, "out": out}

    def op_send_recv(self, r: dict) -> dict:
        snap, ds = self.snap(r["snap"]), self.ds(r["ds"])
        send = subprocess.Popen(["zfs", "send", snap], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        recv = subprocess.run(["zfs", "recv", *(["-F"] if r.get("force") else []), ds], stdin=send.stdout,
                              capture_output=True, text=True, check=False)  # fmt: skip
        send.stdout.close()  # type: ignore[union-attr]
        serr = send.stderr.read().decode(errors="replace") if send.stderr else ""  # type: ignore[union-attr]
        send.wait()
        return {"ok": send.returncode == 0 and recv.returncode == 0, "out": (serr + recv.stdout + recv.stderr).strip()}

    def op_recv_file(self, r: dict) -> dict:
        path, ds = self.path(r["path"]), self.ds(r["ds"])
        with open(path, "rb") as f:
            rc, out = run(["zfs", "recv", ds], stdin=f)
        return {"ok": rc == 0, "out": out}

    def op_write(self, r: dict) -> dict:
        path = self.path(r["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".sgtmp"
        with open(tmp, "w") as f:
            f.write(r["text"])
        os.replace(tmp, path)
        return {"ok": True}

    def op_read(self, r: dict) -> dict:
        out = {}
        for rel in r["paths"]:
            p = self.path(rel, allow_snapdir=True)
            try:
                with open(p) as f:
                    out[rel] = f.read()
            except FileNotFoundError:
                out[rel] = None
        return {"ok": True, "texts": out}

    def op_sha(self, r: dict) -> dict:
        out = {}
        for rel in r["paths"]:
            try:
                with open(self.path(rel), "rb") as f:
                    out[rel] = hashlib.sha256(f.read()).hexdigest()
            except FileNotFoundError:
                out[rel] = None
        return {"ok": True, "sha": out}

    def op_unlink(self, r: dict) -> dict:
        try:
            os.unlink(self.path(r["path"]))
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "out": f"{exc.strerror} ({exc.errno})"}

    def op_chattr(self, r: dict) -> dict:
        rc, out = run(["chattr", "+i" if r["immutable"] else "-i", self.path(r["path"])])
        return {"ok": rc == 0, "out": out}

    def op_files(self, r: dict) -> dict:
        """Every regular file under mnt/ (snapshot directories are hidden), with its immutable flag."""
        top = os.path.join(self.root, "mnt")
        rels = []
        for d, dirs, files in os.walk(top):
            dirs[:] = [x for x in dirs if x != ".zfs"]
            rels += [os.path.relpath(os.path.join(d, f), self.root) for f in files]
        imm = set()
        if rels:
            for chunk in range(0, len(rels), 200):
                part = [os.path.join(self.root, x) for x in rels[chunk:chunk + 200]]
                _, out = run(["lsattr", "-d", *part])
                for line in out.splitlines():
                    flags, _, p = line.partition(" ")
                    if "i" in flags:
                        imm.add(os.path.relpath(p.strip(), self.root))
        return {"ok": True, "files": sorted(rels), "immutable": sorted(imm)}

    def op_inventory(self, r: dict) -> dict:
        if not self.pools:
            return {"ok": True, "items": []}
        rc, out = run(["zfs", "list", "-H", "-p", "-t", "filesystem,snapshot", "-o", "name,type,origin,userrefs",
                       "-r", *self.pools])  # fmt: skip
        if rc != 0:
            return {"ok": False, "out": out}
        items = [dict(zip(("name", "type", "origin", "userrefs"), ln.split("\t"), strict=False)) for ln in out.splitlines()]
        return {"ok": True, "items": items}

    def op_checkpoint(self, r: dict) -> dict:
        rc, out = run(["zpool", "checkpoint", self.pool(r["pool"])])
        return {"ok": rc == 0, "out": out}

    def op_discard(self, r: dict) -> dict:
        rc, out = run(["zpool", "checkpoint", "-d", "-w", self.pool(r["pool"])])
        return {"ok": rc == 0, "out": out}

    def op_rewind(self, r: dict) -> dict:
        pool = self.pool(r["pool"])
        rc, out = run(["zpool", "export", pool])
        if rc != 0:
            return {"ok": False, "out": out}
        rc, out = run(["zpool", "import", "-d", f"{self.root}/vdev", "--rewind-to-checkpoint", pool])
        return {"ok": rc == 0, "out": out}

    def op_env(self, r: dict) -> dict:
        """What the replay ran on (for the report): versions, and the mount check that passed."""
        _, zv = run(["zfs", "version"])
        _, kernel = run(["uname", "-r"])
        osr = {}
        with open("/etc/os-release") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                osr[k] = v.strip('"')
        types = sorted({m[2] for m in mounts()})
        return {"ok": True, "zfs": zv.splitlines(), "kernel": kernel, "os": osr.get("PRETTY_NAME", ""),
                "mount_types": types, "host_paths_visible": any(os.path.exists(p) for p in ("/Users", "/Volumes")),
                "vdev_dir": f"{self.root}/vdev", "cpus": os.cpu_count()}  # fmt: skip

    def op_batch(self, r: dict) -> dict:
        res = []
        for item in r["items"]:
            one = self.handle(item)
            res.append(one)
            if r.get("stop", True) and not one.get("ok"):
                break
        return {"ok": all(x.get("ok") for x in res) and len(res) == len(r["items"]), "results": res}

    def op_reset(self, r: dict | None = None) -> dict:
        """Destroy this session's pools and their vdev files, keep the session (next episode)."""
        errors = self._destroy_pools()
        if not errors:
            for f in os.listdir(f"{self.root}/vdev"):
                os.unlink(f"{self.root}/vdev/{f}")
        return {"ok": not errors, "errors": errors}

    def _destroy_pools(self) -> list[str]:
        errors = []
        for pool in list(self.pools):
            rc, tag = run(["zfs", "get", "-H", "-o", "value", "org.storageguard:session", pool])
            if rc != 0 or tag.strip() != self.token:
                errors.append(f"{pool}: session tag mismatch ({tag!r}); left alone")
                continue
            rc, out = run(["zpool", "destroy", "-f", pool])
            if rc == 0:
                self.pools.remove(pool)
            else:
                errors.append(f"{pool}: {out}")
        return errors

    def op_teardown(self, r: dict | None = None) -> dict:
        errors = self._destroy_pools()
        if not self.pools and not any(m[1].startswith(self.root) for m in mounts()):
            if self.root.startswith(f"{BASE}/s-") and os.path.isdir(self.root):
                shutil.rmtree(self.root)
        return {"ok": not errors, "errors": errors}

    def handle(self, r: dict) -> dict:
        fn = getattr(self, "op_" + str(r.get("op")), None)
        if fn is None:
            return {"ok": False, "refused": True, "out": f"unknown op {r.get('op')!r}"}
        try:
            return fn(r)
        except Refused as exc:
            return {"ok": False, "refused": True, "out": str(exc)}
        except (OSError, KeyError, ValueError, TypeError) as exc:
            return {"ok": False, "out": repr(exc)}


def reap() -> list[str]:
    """Remove pools and directories left by crashed sessions (only ones this tool created)."""
    gone = []
    rc, out = run(["zpool", "list", "-H", "-o", "name"])
    for pool in out.splitlines() if rc == 0 else []:
        if not re.fullmatch(r"sgz[0-9a-f]{8}_[a-z0-9-]+", pool):
            continue
        rc, tag = run(["zfs", "get", "-H", "-o", "value", "org.storageguard:session", pool])
        if rc == 0 and re.fullmatch(r"[0-9a-f]{8}", tag.strip()) and pool.startswith(f"sgz{tag.strip()}_"):
            if run(["zpool", "destroy", "-f", pool])[0] == 0:
                gone.append(pool)
    for d in os.listdir(BASE):
        full = f"{BASE}/{d}"
        if re.fullmatch(r"s-[0-9a-f]{8}", d) and not any(m[1].startswith(full) for m in mounts()):
            shutil.rmtree(full)
            gone.append(full)
    return gone


def main() -> None:
    preflight()
    if "--reap" in sys.argv:
        print(json.dumps({"ok": True, "reaped": reap()}), flush=True)
        return
    s = Session()
    print(json.dumps({"ok": True, "ready": True, "token": s.token, "root": s.root}), flush=True)
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as exc:
                print(json.dumps({"ok": False, "out": f"bad json: {exc}"}), flush=True)
                continue
            if req.get("op") == "exit":
                break
            print(json.dumps(s.handle(req)), flush=True)
    finally:
        s.op_teardown()


if __name__ == "__main__":
    main()
