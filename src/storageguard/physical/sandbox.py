"""A disposable directory that the physical layer may write to and delete from, and nothing else.

Every path is relative, may not contain ``..``, and is checked against the sandbox root;
anything that writes, moves or deletes also resolves the real path (following symlinks)
and refuses unless it lies inside the root, and re-checks the marker file the sandbox was
created with.  Locked (immutable) files use the operating system's immutable flag where
the platform offers it (``chflags uchg`` on macOS/BSD: the kernel itself refuses to delete
or overwrite the file); elsewhere the sandbox enforces the lock itself (``lock_mode``).
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

MARKER = ".sg-sandbox"
MARKER_TEXT = "StorageGuard physical sandbox: disposable test data, safe to delete.\n"
PREFIX = "sg-sandbox-"


class SandboxError(RuntimeError):
    """A sandbox rule was violated (path escape, symlink, locked file, missing marker)."""


def _os_lock_supported(root: str) -> bool:
    if not hasattr(os, "chflags") or not hasattr(stat, "UF_IMMUTABLE"):
        return False
    probe = os.path.join(root, ".lock-probe")
    with open(probe, "w") as fh:
        fh.write("probe")
    try:
        os.chflags(probe, stat.UF_IMMUTABLE)
        try:
            os.unlink(probe)
        except PermissionError:
            return True
        return False  # flag accepted but not enforced (e.g. running as root)
    except OSError:
        return False
    finally:
        if os.path.exists(probe):
            os.chflags(probe, 0)
            os.unlink(probe)


class Sandbox:
    def __init__(self, root: str | Path, lock_mode: str, locks: set[str] | None = None) -> None:
        self.root = Path(root)
        self._root = os.path.realpath(str(root))
        self.lock_mode = lock_mode  # "os" | "emulated"
        self.locks: set[str] = set(locks or ())
        self._check_marker()

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, base: str | Path | None = None) -> Sandbox:
        if base is not None:
            os.makedirs(base, exist_ok=True)
        root = os.path.realpath(tempfile.mkdtemp(prefix=PREFIX, dir=base))
        with open(os.path.join(root, MARKER), "w") as fh:
            fh.write(MARKER_TEXT)
        return cls(root, "os" if _os_lock_supported(root) else "emulated")

    def copy(self, base: str | Path | None = None) -> Sandbox:
        """An independent copy (same files, same locks) for a destructive drill."""
        new = Sandbox.create(base if base is not None else os.path.dirname(self._root))
        for dirpath, dirnames, filenames in os.walk(self._root):
            rel_dir = os.path.relpath(dirpath, self._root)
            dst_dir = new._root if rel_dir == "." else os.path.join(new._root, rel_dir)
            for d in dirnames:
                if os.path.islink(os.path.join(dirpath, d)):
                    raise SandboxError(f"symlink in sandbox: {os.path.join(rel_dir, d)}")
                os.makedirs(os.path.join(dst_dir, d), exist_ok=True)
            for f in filenames:
                src = os.path.join(dirpath, f)
                if os.path.islink(src):
                    raise SandboxError(f"symlink in sandbox: {os.path.join(rel_dir, f)}")
                if rel_dir == "." and f == MARKER:
                    continue
                shutil.copyfile(src, os.path.join(dst_dir, f))
        for rel in sorted(self.locks):
            new.lock(rel)
        return new

    def destroy(self) -> None:
        self._check_marker()
        for rel in list(self.locks):
            self.unlock(rel)
        shutil.rmtree(self._root)

    # ------------------------------------------------------------------ safety
    def _check_marker(self) -> None:
        if not os.path.basename(self._root).startswith(PREFIX):
            raise SandboxError(f"not a sandbox directory: {self._root}")
        m = os.path.join(self._root, MARKER)
        try:
            with open(m) as fh:
                ok = fh.read() == MARKER_TEXT
        except OSError:
            ok = False
        if not ok:
            raise SandboxError(f"sandbox marker missing in {self._root}")

    def _p(self, rel: str) -> str:
        """Lexical check: a relative path without ``..`` (enough for reading)."""
        if not rel or rel.startswith("/") or ".." in rel.split("/") or "\\" in rel:
            raise SandboxError(f"bad sandbox path: {rel!r}")
        return os.path.join(self._root, rel)

    def _w(self, rel: str) -> str:
        """Strict check for anything that writes or deletes: the real path must be inside the root."""
        p = self._p(rel)
        real = os.path.realpath(p)
        if not real.startswith(self._root + os.sep) or os.path.islink(p):
            raise SandboxError(f"path escapes the sandbox: {rel!r}")
        return p

    def path(self, rel: str) -> Path:
        return Path(self._w(rel))

    def _refuse_locked(self, rel: str, what: str) -> None:
        if rel in self.locks:
            raise PermissionError(f"{what} refused: {rel} is immutable")

    def _under(self, rel: str) -> set[str]:
        pre = rel.rstrip("/") + "/"
        return {x for x in self.locks if x == rel or x.startswith(pre)}

    # ------------------------------------------------------------------ queries
    def exists(self, rel: str) -> bool:
        return os.path.exists(self._p(rel))

    def read(self, rel: str) -> str:
        with open(self._p(rel)) as fh:
            return fh.read()

    def size(self, rel: str) -> int:
        return os.stat(self._p(rel)).st_size

    def files(self, under: str | None = None, suffix: str = ".img") -> list[str]:
        top = self._p(under) if under else self._root
        out = []
        for dirpath, _, filenames in os.walk(top):
            rel_dir = os.path.relpath(dirpath, self._root)
            out += [f if rel_dir == "." else f"{rel_dir}/{f}" for f in filenames if f.endswith(suffix)]
        return sorted(out)

    # ------------------------------------------------------------------ mutations
    def write(self, rel: str, text: str) -> None:
        """Atomic create-or-replace (refused for a locked file)."""
        self._refuse_locked(rel, "overwrite")
        p = self._w(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(text)
        os.replace(tmp, p)

    def copy_file(self, src: str, dst: str) -> None:
        self.write(dst, self.read(src))

    def append(self, rel: str, line: str) -> None:
        self._refuse_locked(rel, "write")
        with open(self._w(rel), "a") as fh:
            fh.write(line + "\n")

    def unlink(self, rel: str) -> int:
        """Delete one file for real; returns the bytes freed.  A locked file raises PermissionError."""
        self._check_marker()
        p = self._w(rel)
        n = os.stat(p).st_size
        if self.lock_mode == "emulated":
            self._refuse_locked(rel, "delete")
        os.unlink(p)  # in "os" mode the kernel enforces the lock
        return n

    def rmtree(self, rel: str, *, destroy_locked: bool = False) -> list[tuple[str, int]]:
        """Delete a directory tree for real; returns ``(file, bytes)`` for every file removed.

        A tree holding a locked file is refused, unless ``destroy_locked`` (a drill's fire or
        flood: physical destruction does not respect locks).
        """
        self._check_marker()
        top = self._w(rel)
        locked = self._under(rel)
        if locked and not destroy_locked:
            raise PermissionError(f"delete refused: {rel} contains immutable file(s) {sorted(locked)}")
        for x in locked:
            self.unlock(x)
        removed = [(f, os.stat(self._p(f)).st_size) for f in self.files(rel, "")]
        shutil.rmtree(top)
        return removed

    def move(self, src: str, dst: str) -> None:
        """Rename a directory or file inside the sandbox (locks travel with it)."""
        s, d = self._w(src), self._w(dst)
        if os.path.exists(d):
            raise SandboxError(f"move target exists: {dst}")
        os.makedirs(os.path.dirname(d), exist_ok=True)
        os.replace(s, d)
        moved = self._under(src)
        self.locks = (self.locks - moved) | {dst + x[len(src):] for x in moved}

    def lock(self, rel: str) -> None:
        p = self._w(rel)
        if self.lock_mode == "os":
            os.chflags(p, stat.UF_IMMUTABLE)
        else:
            os.chmod(p, 0o444)
        self.locks.add(rel)

    def unlock(self, rel: str) -> None:
        p = self._w(rel)
        if os.path.exists(p):
            if self.lock_mode == "os":
                os.chflags(p, 0)
            else:
                os.chmod(p, 0o644)
        self.locks.discard(rel)
