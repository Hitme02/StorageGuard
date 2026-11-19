"""OpenZFS replay: the sandbox rules of the in-VM agent and the host-side checks.

These run on any machine (no VM needed).  The end-to-end replay test runs only when the
isolated Lima VM is available: set ``SG_ZFS_VM=1``.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from storageguard.physical import zfs, zfs_agent
from storageguard.physical.zfs_agent import CIDATA, Refused, Session, mount_problems


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(zfs_agent, "BASE", str(tmp_path))
    s = Session()
    s.pools.append(f"sgz{s.token}_arr-1")
    return s


def test_mount_check_refuses_host_and_network_file_systems():
    safe = [("/dev/vda1", "/", "ext4", "rw"), ("fusectl", "/sys/fs/fuse/connections", "fusectl", "rw"),
            ("/dev/vdb", CIDATA[0], CIDATA[1], "ro,relatime"), ("tmpfs", "/run", "tmpfs", "rw")]  # fmt: skip
    assert mount_problems(safe) == []
    for bad in [("mount0", "/Users/me", "virtiofs", "rw"), ("h", "/tmp/lima", "9p", "rw"),
                ("u@h:/", "/mnt/x", "fuse.sshfs", "rw"), ("srv:/x", "/data", "nfs4", "rw"),
                ("//srv/x", "/data", "cifs", "rw"), ("/dev/vdc", "/mnt/extra", "ext4", "rw"),
                ("/dev/vdb", CIDATA[0], CIDATA[1], "rw")]:  # fmt: skip
        assert mount_problems([*safe, bad]), bad


def test_names_must_belong_to_the_session(session):
    own = f"sgz{session.token}_arr-1"
    assert session.ds(f"{own}/pool-1/vol-1") and session.snap(f"{own}/pool-1/vol-1@snap-1")
    for bad in ["rpool/ROOT", f"sgz{session.token}_arr-2/x", "sgz00000000_arr-1/pool-1", f"{own}/../x",
                f"{own}/pool-1/vol 1", f"{own}/pool-1;rm -rf /", "/dev/vda"]:  # fmt: skip
        with pytest.raises(Refused):
            session.ds(bad)
    with pytest.raises(Refused):
        session.snap(f"{own}/pool-1/vol-1")  # not a snapshot


def test_paths_stay_inside_the_session(session, tmp_path):
    assert session.path("mnt/arr-1/pool-1/vol-1/data.img").startswith(session.root)
    for bad in ["../x", "/etc/passwd", "mnt/../../etc/passwd", "vdev/arr-1.img", "mnt/arr-1/./x", "mnt//x",
                "mnt/arr-1/vol/.zfs/snapshot/s/data.img"]:  # fmt: skip
        with pytest.raises(Refused):
            session.path(bad)
    assert session.path("mnt/arr-1/vol/.zfs/snapshot/s/data.img", allow_snapdir=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.makedirs(f"{session.root}/mnt/arr-1", exist_ok=True)
    os.symlink(outside, f"{session.root}/mnt/arr-1/link")
    with pytest.raises(Refused):
        session.path("mnt/arr-1/link/data.img")


def test_closed_set_of_operations(session):
    own = f"sgz{session.token}_arr-1"
    assert session.handle({"op": "system", "cmd": "rm -rf /"})["refused"]
    assert session.handle({"op": "destroy", "target": own})["refused"]  # never a pool's root dataset
    assert session.handle({"op": "destroy", "target": f"{own}/p/v", "flags": "f"})["refused"]
    assert session.handle({"op": "destroy", "target": "rpool/ROOT/ubuntu", "flags": "R"})["refused"]
    session.pools.append(f"sgz{session.token}_arr-2")
    assert session.handle({"op": "rename", "src": f"{own}/p/v", "dst": f"sgz{session.token}_arr-2/p/v"})["refused"]
    assert session.handle({"op": "pool_create", "name": "../../dev/vda"})["refused"]
    assert session.handle({"op": "checkpoint", "pool": "rpool"})["refused"]


def _limactl_list(monkeypatch, info):
    def fake(argv, **kw):
        assert argv[:2] == ["limactl", "list"]
        return subprocess.CompletedProcess(argv, 0, json.dumps(info) + "\n", "")

    monkeypatch.setattr(zfs.subprocess, "run", fake)


def test_host_refuses_a_vm_with_mounts_or_port_forwards(monkeypatch):
    good = {"name": "sg-zfs", "status": "Running", "vmType": "vz", "config": {"plain": True, "mounts": None}}
    _limactl_list(monkeypatch, good)
    assert zfs.check_vm()["name"] == "sg-zfs"
    for cfg in [{"plain": True, "mounts": [{"location": "~"}]}, {"plain": False}, {"plain": True, "portForwards": [{}]}]:
        _limactl_list(monkeypatch, {**good, "config": cfg})
        with pytest.raises(zfs.ZfsUnavailable):
            zfs.check_vm()
    _limactl_list(monkeypatch, {**good, "status": "Stopped"})
    with pytest.raises(zfs.ZfsUnavailable):
        zfs.check_vm()


def test_outcome_classes():
    from storageguard.eval.zfs_report import outcome

    assert outcome({"twin_ok": True, "native": "ok"}) == "both executed"
    assert outcome({"twin_ok": True, "native": "refused"}) == "ZFS refused, twin executed"
    assert outcome({"twin_ok": False, "native": "refused"}) == "both refused"
    assert outcome({"twin_ok": False, "native": "ok"}) == "ZFS executed, twin refused"
    assert outcome({"twin_ok": True, "native": "emulated"}) == "emulated in ZFS"
    assert outcome({"twin_ok": True, "native": "no-data-effect"}) == "no ZFS counterpart"
    assert outcome({"twin_ok": False, "native": "ok"}, "volume is already in the recycle bin") == "emulated in ZFS"


@pytest.mark.skipif(os.environ.get("SG_ZFS_VM") != "1", reason="needs the isolated Lima VM (set SG_ZFS_VM=1)")
def test_end_to_end_on_openzfs():
    from storageguard.bench.splits import load_bench, task_twin
    from storageguard.eval.physical_eval import PhysicalOptions

    task = next(t for t in load_bench() if t.trap_type == "T3" and t.split == "dev")
    calls = [("delete_snapshot", {"snapshot_id": sid}) for sid in list(task_twin(task).state.snapshots)[:2]]
    agent = zfs.Agent()
    try:
        world = zfs.ZfsWorld(agent, task_twin(task))
        assert world.start_cmp["identical"] and not world.start_drift
        out = zfs.run_trajectory(world, lambda: task_twin(task), calls, task.allowed_changes,
                                 PhysicalOptions().reference_for(task, calls))  # fmt: skip
        world.close()
    finally:
        agent.close()
    assert out["end"]["identical"] and out["n_drift"] == 0
    assert {k: out["file"][k] for k in ("phys_data", "phys_unsafe")} == {k: out["zfs"][k] for k in ("phys_data", "phys_unsafe")}
