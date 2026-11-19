"""On-disk format of a sandbox image (volume, snapshot, clone, replica or backup).

An image is a text file::

    SGIMG 1
    C <lineage> <minute> <payload> <mac>      one committed write
    X <hex>                                   an encrypted (unreadable) segment

A *lineage* is one dataset's write history; a copy of a lineage at point in time
``p`` holds every commit of that lineage up to ``p``.  A commit is readable only if
its MAC verifies, so a recovery check cannot be fooled by a damaged or encrypted
file: what counts is what can actually be read back.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

MAGIC = "SGIMG 1"
COMMIT_STEP_MIN = 240  # one background commit every 4 simulated hours, plus the exact point in time


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def commit_line(lineage: str, minute: int) -> str:
    payload = _h(f"{lineage}|{minute}|payload")[:32]
    return f"C {lineage} {minute} {payload} {_h(f'{lineage}|{minute}|{payload}')[:16]}"


def commit_times(start: int, point: int, step: int = COMMIT_STEP_MIN) -> list[int]:
    """Background commits on a fixed grid in ``[start, point]``, plus ``point`` itself."""
    first = -(-start // step) * step
    out = list(range(first, point + 1, step))
    if not out or out[-1] != point:
        out.append(point)
    return out


def segment(lineage: str, start: int, point: int) -> list[str]:
    return [commit_line(lineage, t) for t in commit_times(start, point)]


def _keystream(key: str, n: int) -> bytes:
    out, i = bytearray(), 0
    while len(out) < n:
        out += hashlib.sha256(f"{key}|{i}".encode()).digest()
        i += 1
    return bytes(out[:n])


def encrypt_lines(lines: list[str], key: str) -> str:
    """What ransomware leaves behind: the same bytes, unreadable without the key."""
    raw = "\n".join(lines).encode()
    return "X " + bytes(a ^ b for a, b in zip(raw, _keystream(key, len(raw)), strict=True)).hex()


def render(lines: list[str]) -> str:
    return "\n".join([MAGIC, *lines]) + "\n"


@dataclass
class ImageContent:
    """What can be read back from one image file."""

    valid: bool  # the file is an image at all
    newest: dict[str, int]  # lineage -> newest readable commit
    encrypted_segments: int
    bad_commits: int

    @property
    def head(self) -> int | None:
        return max(self.newest.values()) if self.newest else None


def parse(text: str) -> ImageContent:
    lines = text.splitlines()
    if not lines or lines[0] != MAGIC:
        return ImageContent(False, {}, 0, 0)
    newest: dict[str, int] = {}
    enc = bad = 0
    for line in lines[1:]:
        if line.startswith("X "):
            enc += 1
            continue
        parts = line.split(" ")
        if len(parts) != 5 or parts[0] != "C":
            bad += 1
            continue
        _, lineage, minute, payload, mac = parts
        if not minute.isdigit() or mac != _h(f"{lineage}|{minute}|{payload}")[:16]:
            bad += 1
            continue
        t = int(minute)
        if t > newest.get(lineage, -1):
            newest[lineage] = t
    return ImageContent(True, newest, enc, bad)


def body(text: str) -> list[str]:
    """The lines after the header (for appending and re-encrypting)."""
    lines = text.splitlines()
    return lines[1:] if lines and lines[0] == MAGIC else lines
