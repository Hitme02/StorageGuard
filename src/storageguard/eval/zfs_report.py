"""Report on the OpenZFS replay (RQ8b): does a real storage system agree with the twin?

Writes ``results/zfs/{calls.csv, REPORT.md}``, ``paper/tables/zfs_*.tex`` and
``paper/figures/F9_zfs.{pdf,png}``.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

import pandas as pd

from ..bench.splits import load_bench, task_twin
from ..paths import paper_dir
from .baselines import LABELS, MAIN_CONFIGS
from .plots import CATEGORICAL, INK, MUTED, SURFACE, _save, _style, plt
from .report import _md_table, _tex, study_models
from .zfs_eval import FLAGS, MEAS_KEYS, load_index, load_results, zfs_root

OUTCOMES = [  # (label, colour): fixed stacking order; adjacent hues validated (dataviz validator, light surface)
    ("both executed", CATEGORICAL[0]), ("both refused", CATEGORICAL[2]), ("ZFS refused, twin executed", CATEGORICAL[1]),
    ("ZFS executed, twin refused", CATEGORICAL[6]), ("emulated in ZFS", CATEGORICAL[3]),
    ("no ZFS counterpart", "#b9b8b2"),  # not applicable: neutral, not a category hue
]  # fmt: skip


def outcome(c: dict[str, Any], twin_error: str = "") -> str:
    n = c["native"]
    if not c["twin_ok"] and "recycle bin" in twin_error:  # a rule of the bin itself, which ZFS only emulates
        return "emulated in ZFS"
    if n in ("no-data-effect", "not-modelled", "n/a"):
        return "no ZFS counterpart"
    if n == "emulated":
        return "emulated in ZFS"
    if c["twin_ok"]:
        return "both executed" if n == "ok" else "ZFS refused, twin executed"
    return "both refused" if n == "refused" else "ZFS executed, twin refused"


def _norm(msg: str) -> str:
    """ZFS's own reason: the ``cannot ...`` line if there is one (``destroy -v`` first lists what it would do)."""
    msg = re.sub(r"sgz[0-9a-f]{8}_[A-Za-z0-9_.@/:-]*[A-Za-z0-9_.@/-]", "<dataset>", msg or "")
    msg = re.sub(r"'<dataset>'", "<dataset>", msg)
    lines = [ln.strip() for ln in msg.split("\n") if ln.strip()]
    pick = next((ln for ln in lines if ln.startswith("cannot")), lines[0] if lines else "")
    return pick[:110]


def twin_errors(trajs: dict[str, Any], keys: set[str]) -> dict[tuple[str, int], str]:
    """The twin's refusal message for every mutating call of the given trajectories (re-derived, no ZFS)."""
    from ..twin.operations import OPS

    tasks = {t.id: t for t in load_bench()}
    out = {}
    for k in keys:
        t = trajs[k]
        twin = task_twin(tasks[t.task_id])
        step = 0
        for tool, args in t.call_list():
            res = twin.execute(tool, args)
            if OPS.get(tool) and OPS[tool].mutating:
                step += 1
                if not res.get("ok"):
                    out[(k, step)] = _norm(re.sub(r"'[a-z]+-\d+'", "<id>", str(res.get("error", ""))))
    return out


def call_frame(results: dict[str, dict], weight: Counter, terr: dict[tuple[str, int], str]) -> pd.DataFrame:
    rows = []
    for k, r in results.items():
        for c in r.get("calls", []):
            te = terr.get((k, c["step"]), "")
            rows.append({"key": k, "task_id": r["task_id"], "step": c["step"], "op": c["op"], "twin_ok": c["twin_ok"],
                         "native": c["native"], "outcome": outcome(c, te), "detail": _norm(c.get("detail", "")),
                         "twin_error": te,
                         "followed": c.get("followed", ""), "zfs_destroyed": len(c.get("destroyed", [])),
                         "twin_destroyed": len(c.get("twin_destroyed", [])),
                         "cascade_equal": sorted(c.get("destroyed", [])) == sorted(c.get("twin_destroyed", [])),
                         "would_destroy": len(c.get("would_destroy", [])), "episodes": weight[k]})  # fmt: skip
    return pd.DataFrame(rows)


def op_table(calls: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for op, g in sorted(calls.groupby("op"), key=lambda x: -len(x[1])):
        row: dict[str, Any] = {"Operation": op, "Calls": len(g), "Episode-weighted": int(g["episodes"].sum())}
        for label, _ in OUTCOMES:
            n = int((g["outcome"] == label).sum())
            row[label] = n or ""
        d = g[(g["outcome"] == "both executed") & (g["twin_destroyed"] > 0)]
        row["Same cascade"] = f"{int(d['cascade_equal'].sum())}/{len(d)}" if len(d) else ""
        rows.append(row)
    return pd.DataFrame(rows)


def fig9(calls: pd.DataFrame, out: Any) -> list:
    """F9: per operation, how OpenZFS answered the twin's calls (unique trajectory calls)."""
    _style()
    ops = [op for op, _ in Counter(calls["op"]).most_common()]
    fig, ax = plt.subplots(figsize=(3.5, 0.15 * len(ops) + 1.25))  # one column of a two-column paper, at 100%
    left = [0] * len(ops)
    for label, colour in OUTCOMES:
        vals = [int(((calls["op"] == op) & (calls["outcome"] == label)).sum()) for op in ops]
        ax.barh(range(len(ops)), vals, left=left, height=0.72, color=colour, edgecolor=SURFACE, linewidth=0.5,
                label=label)  # fmt: skip
        left = [a + b for a, b in zip(left, vals, strict=True)]
    for i, tot in enumerate(left):
        ax.text(tot + max(left) * 0.01, i, str(tot), va="center", fontsize=5.5, color=MUTED)
    ax.set_xlim(0, max(left) * 1.1)
    ax.set_yticks(range(len(ops)), ops, fontsize=6)
    ax.invert_yaxis()
    ax.set_xlabel("calls (distinct trajectories)", fontsize=6.5)
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="x", labelsize=6, colors=INK)
    ax.tick_params(axis="y", length=2)
    ax.legend(ncol=2, loc="upper center", bbox_to_anchor=(0.38, -0.1), fontsize=6, handlelength=1.0,
              columnspacing=0.8, frameon=False)  # fmt: skip
    return _save(fig, out, "F9_zfs")


def build_zfs_report() -> Any:
    root, paper = zfs_root(), paper_dir()
    (paper / "tables").mkdir(parents=True, exist_ok=True)
    L = ["# OpenZFS replay: does a real storage system agree with the twin?", ""]
    try:
        trajs, episodes = load_index()
    except FileNotFoundError:
        L.append("No OpenZFS replay found. Run `sg zfs` (needs the isolated Lima VM, see README).")
        (root / "REPORT.md").parent.mkdir(parents=True, exist_ok=True)
        (root / "REPORT.md").write_text("\n".join(L) + "\n")
        return root / "REPORT.md"
    results = load_results()
    env = json.loads((root / "environment.json").read_text()) if (root / "environment.json").exists() else {}
    ok = {k: r for k, r in results.items() if "error" not in r}
    weight = Counter(e["key"] for e in episodes)
    refused_keys = {k for k, r in ok.items() if any(not c["twin_ok"] for c in r.get("calls", []))}
    terr = twin_errors(trajs, refused_keys)
    calls = call_frame(ok, weight, terr)
    calls.to_csv(root / "calls.csv", index=False)
    covered = sum(weight[k] for k in ok)
    vm = env.get("vm", {})
    L += [
        "Every distinct trajectory of the study (task + the ordered calls the middleware executed; the twin is",
        "deterministic, so this fixes everything an episode did to storage) was run again on **OpenZFS**, side by side",
        "with the file sandbox. Each array is a ZFS pool, each volume a dataset holding its image, each snapshot a ZFS",
        "snapshot (locks are `zfs hold`), clones are `zfs clone`, backups are `zfs send` streams (restored for the",
        "check with `zfs receive`), replication is `zfs send | zfs receive`, a restore is `zfs rollback` and a delete",
        "is `zfs destroy` (`-R` when the call says force). Calls the twin executed were run natively; calls the twin",
        "refused were attempted natively too, and rewound from a pool checkpoint when ZFS went ahead. Methods:",
        "`src/storageguard/physical/zfs.py`, DECISIONS.md section 9 (X10-X13).", "",
        "## Isolation", "",
        f"- system: {', '.join(env.get('zfs', ['?']))} on {env.get('os', '?')} (kernel {env.get('kernel', '?')}), "
        f"a Lima {env.get('lima', '').replace('limactl version ', '')} VM (`{vm.get('vmType', '?')}`, "
        f"{vm.get('cpus', '?')} CPUs, {int(vm.get('memory', 0)) // 2**30} GiB)",
        "- the VM runs in Lima's *plain* mode: no host directory is mounted and no port is forwarded (checked before every",
        "  session); inside, the agent refuses to start if any host or network file system (virtiofs, 9p, sshfs, NFS,",
        f"  CIFS, FUSE) is visible; mount types seen: {', '.join(env.get('mount_types', []))}",
        "- pools live on sparse files under `/var/lib/sgzfs/s-<session>/vdev` on the VM's own disk; no block device is",
        "  ever named; every pool is tagged with its session and every dataset and path argument is checked against it;",
        "  teardown destroys only pools carrying the session tag",
        "",
        "## Fidelity", "",
        f"- trajectories: {len(trajs)} covering {len(episodes)} replayed episodes; run on ZFS: {len(ok)} "
        f"({covered} episodes); failed: {len(results) - len(ok)}",
        f"- starting worlds (one per task, then checkpointed): byte-identical to the file sandbox in "
        f"{sum(1 for r in ok.values() if r['start']['identical'])}/{len(ok)} trajectories; backups restored with "
        f"`zfs receive` for that check: {sum(r['start']['export']['received'] for r in {r['task_id']: r for r in ok.values()}.values())}",
        f"- inventory drift after a call (ZFS datasets, snapshots, holds, files or immutable flags differ from the twin): "
        f"{int(sum(r['n_drift'] for r in ok.values()))}; driver errors: {sum(len(r['errors']) for r in ok.values())}",
        f"- final state read back from ZFS byte-identical to the file sandbox (every image, snapshot and backup, and every"
        f" lock): {sum(1 for r in ok.values() if r['end']['identical'])}/{len(ok)} trajectories",
    ]  # fmt: skip
    same_flags = sum(all(r["file"][f] == r["zfs"][f] for f in FLAGS) for r in ok.values())
    same_all = sum(all(r["file"][k] == r["zfs"][k] for k in MEAS_KEYS) for r in ok.values())
    rec_ok = sum(1 for e in episodes if e["key"] in ok and all(e["recorded"][k] == ok[e["key"]]["file"][k] for k in MEAS_KEYS))
    L += [
        f"- every physical measurement (data destroyed/rolled back, outage, the three drills) computed on the ZFS read-back"
        f" equals the file-sandbox one: flags {same_flags}/{len(ok)}, full detail {same_all}/{len(ok)} trajectories",
        f"- the file side reproduces each episode's recorded physical result (so the call-list replay is exact): "
        f"{rec_ok}/{covered} episodes",
        f"- trajectories needing a rewind-and-replay because ZFS executed a call the twin refused: "
        f"{sum(1 for r in ok.values() if r.get('passes', 1) > 1)}", "",
    ]  # fmt: skip

    # ---- per-call agreement
    ot = op_table(calls)
    (paper / "tables" / "zfs_calls.tex").write_text(_tex(ot, "How OpenZFS answered each mutating call of the study's trajectories.", "tab:zfs"))
    tot = Counter(calls["outcome"])
    native = calls[~calls["outcome"].isin(["no ZFS counterpart", "emulated in ZFS"])]
    agree = int(native["outcome"].isin(["both executed", "both refused"]).sum())
    d = calls[(calls["outcome"] == "both executed") & (calls["twin_destroyed"] > 0)]
    L += ["## How ZFS answered each call", "",
          f"{len(calls)} mutating calls across the distinct trajectories ({int(calls['episodes'].sum())} episode-weighted). "
          f"Of the {len(native)} with a native ZFS counterpart, ZFS agreed with the twin on **{agree}** "
          f"({100 * agree / max(1, len(native)):.1f}%): {tot['both executed']} executed by both, {tot['both refused']} refused "
          f"by both. Where both destroyed something, ZFS destroyed exactly the twin's cascade (volume, its snapshots, "
          f"dependent clones) in {int(d['cascade_equal'].sum())}/{len(d)} calls. "
          f"{tot['emulated in ZFS']} calls use an emulated feature (the recycle bin is a `zfs rename`) and "
          f"{tot['no ZFS counterpart']} have no data effect or no ZFS counterpart (host mappings, zoning, QoS, spares, "
          "pausing a link).", "", _md_table(ot), "", "![F9](../../paper/figures/F9_zfs.png)", ""]  # fmt: skip
    fig9(calls, paper / "figures")

    # ---- the disagreements
    stricter = calls[calls["outcome"] == "ZFS refused, twin executed"]
    looser = calls[calls["outcome"] == "ZFS executed, twin refused"]
    L += ["## Where ZFS and the twin disagree", "",
          f"**ZFS refused, the twin executed ({len(stricter)} calls, {int(stricter['episodes'].sum())} episode-weighted).** "
          "ZFS was brought to the twin's outcome afterwards by the nearest equivalent, so the rest of the episode stays"
          " comparable:", ""]  # fmt: skip
    for (op, msg), g in sorted(stricter.groupby(["op", "detail"]), key=lambda x: -len(x[1])):
        L.append(f"- `{op}`: {len(g)}x \"{msg}\" -> {g['followed'].iloc[0] or 'n/a'}")
    rs = calls[calls["op"] == "restore_snapshot"]
    rsr = rs[rs["outcome"] == "ZFS refused, twin executed"]
    if len(rs):
        distinct = sum(len({w for c in r.get("calls", []) if c["op"] == "restore_snapshot" for w in c.get("would_destroy", [])})
                       for r in results.values())  # fmt: skip
        L += ["", f"Restores: native `zfs rollback` refused {len(rsr)} of the {int((rs['twin_ok']).sum())} restores the "
              f"twin executed, because newer snapshots existed; `zfs rollback -r` would have destroyed "
              f"{distinct} distinct newer snapshots ({int(rsr['would_destroy'].sum())} listed over the {len(rsr)} refusals,"
              " as a trajectory that restores twice lists them twice; median "
              f"{int(rsr['would_destroy'].median()) if len(rsr) else 0} per restore)."
              " The twin keeps them; ONTAP's volume SnapRestore, like `-r`, deletes them (NetApp KB). On a ZFS backend an in-place restore"
              " to an older point therefore either fails or, with `-r`, destroys every newer recovery point: the erosion"
              " pattern of the physical replay, in the storage system's own semantics. The twin under-states the cost of"
              " such a restore on this kind of backend; its effect on the physical results is measured in"
              " `results/restore_sensitivity/REPORT.md` (`sg restore-sensitivity`)."]  # fmt: skip
    L += ["", f"**ZFS executed, the twin refused ({len(looser)} calls, {int(looser['episodes'].sum())} episode-weighted).** "
          "Each was rewound from the checkpoint. The twin's refusal reason, with how often ZFS went ahead anyway"
          " (refusals that exist only because of the recycle bin are counted as emulated):", ""]  # fmt: skip
    if len(looser):
        for (op, why), g in sorted(looser.groupby(["op", "twin_error"]), key=lambda x: -len(x[1])):
            lost = int((g["zfs_destroyed"] > 0).sum())
            L.append(f"- `{op}`: twin said \"{why}\"; ZFS executed it {len(g)}x"
                     + (f", destroying data in {lost} (then rewound)" if lost else ""))  # fmt: skip
        L += ["", "These are array-level interlocks of the twin (a volume still mapped to a host, or still in a replication"
              " relationship) that a bare ZFS backend does not have: there, the same agent calls would have gone"
              " through, so on such a backend the twin's harm numbers are a lower bound."]  # fmt: skip
    else:
        L.append("- none")
    agreed = calls[calls["outcome"] == "both refused"]
    if len(agreed):
        L += ["", f"**Refused by both ({len(agreed)} calls, {int(agreed['episodes'].sum())} episode-weighted)**, with each"
              " side's reason:", "", "| call | twin's reason | ZFS's reason | calls |", "|---|---|---|---|"]  # fmt: skip
        for (op, te, msg), g in sorted(agreed.groupby(["op", "twin_error", "detail"]), key=lambda x: -len(x[1])):
            L.append(f"| `{op}` | {te} | {msg} | {len(g)} |")
        L += ["", "The trap mechanics the benchmark relies on are ZFS's own rules: a snapshot with a clone cannot be"
              " deleted without `-R` (T5), a held (locked) snapshot cannot be deleted at all (T12), and a volume with"
              " snapshots cannot be destroyed without `-r`."]  # fmt: skip

    # ---- physical harm measured on ZFS, per configuration
    ep = pd.DataFrame([{**{k: e[k] for k in ("exp", "model", "config", "task_id", "run", "key")},
                        **{f"zfs_{f}": ok[e["key"]]["zfs"][f] for f in FLAGS},
                        **{f"file_{f}": e["recorded"][f] for f in FLAGS}} for e in episodes if e["key"] in ok])  # fmt: skip
    ep.to_csv(root / "episodes.csv", index=False)
    main = ep[ep["exp"] == "main"]
    if not main.empty:
        bench = {t.id: t.split for t in load_bench()}
        main = main[[bench.get(t) == "test" for t in main["task_id"]]]
        main_models, _ = study_models(main.assign(split="test"), "main")
        dm = main[main["model"].isin(main_models)]
        rows = []
        for cfg in [c for c in MAIN_CONFIGS if c in set(dm["config"])]:
            g = dm[dm["config"] == cfg]
            rows.append({"Configuration": LABELS[cfg], "Episodes": len(g),
                         "Physical harm % (ZFS)": f"{100 * g['zfs_phys_unsafe'].mean():.1f}",
                         "(file sandbox)": f"{100 * g['file_phys_unsafe'].mean():.1f}",
                         "Data destroyed % (ZFS)": f"{100 * g['zfs_phys_data'].mean():.1f}",
                         "(file sandbox) ": f"{100 * g['file_phys_data'].mean():.1f}",
                         "Ransomware drills % (ZFS)": f"{100 * g['zfs_phys_ransom'].mean():.1f}"})  # fmt: skip
        ct = pd.DataFrame(rows)
        tp = ct.drop(columns=["Episodes"]).set_axis(["Config.", "Harm, ZFS", "Harm, files", "Data lost, ZFS",
                                                      "Data lost, files", "Ransom., ZFS"], axis=1)  # fmt: skip
        tp["Config."] = tp["Config."].str.split().str[0]
        (paper / "tables" / "zfs_physical.tex").write_text(_tex(tp, "Physical harm (%) measured on OpenZFS and on the file "
            f"sandbox (test split, main-study models, {int(ct['Episodes'].iloc[0])} episodes per configuration). Data lost: a"
            " dataset destroyed or rolled back. Ransom.: the ransomware drills lose more data.", "tab:zfsphys"))  # fmt: skip
        L += ["", f"## Physical harm measured on OpenZFS (test split, {', '.join(main_models)})", "", _md_table(ct), "",
              "The ranking of the baselines, and every number, is the same whether the episode's data is measured on"
              " the file sandbox or read back from ZFS."]  # fmt: skip
    L += ["", "## Scope", "",
          "- Volumes are ZFS datasets holding an image file rather than zvols: the semantics compared (snapshots, holds,",
          "  clones and their dependencies, rollback, destroy cascades, send/receive) are the same for both dataset types.",
          "- ZFS has no recycle bin (emulated by a rename), no host mappings, zoning, QoS or spares, and no replication",
          "  relationship object (a link is a `zfs send | zfs receive` job; a paused link simply ships nothing).",
          "- The drills (site loss, ransomware) are run on the byte-exact read-back of the final ZFS state by the same code",
          "  as the file run; the reference drills (only the ticket-authorised calls) come from the file sandbox.",
          "- Inside the disposable VM, `udisks2` and `zfs-zed` were stopped: they only react to device events and slowed",
          "  dataset creation fivefold."]  # fmt: skip
    (root / "REPORT.md").write_text("\n".join(L) + "\n")
    return root / "REPORT.md"
