"""Report on the physical replay: what each configuration really destroyed, and how the oracle compares.

Writes ``results/physical/{episodes.csv, REPORT.md}``, ``paper/tables/physical*.tex`` and
``paper/figures/F8_physical.{pdf,png}``.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from ..paths import paper_dir, results_dir
from .baselines import LABELS, MAIN_CONFIGS
from .physical_eval import load_physical_rows
from .plots import CONFIG_COLOR, HATCH, INK, MUTED, SURFACE, _save, _style, plt
from .report import _md_table, _tex, p_text, study_models
from .stats import aggregate, mcnemar, paired_bootstrap

PHYS_METRICS = ["unsafe", "oracle_h123", "phys_unsafe", "phys_data", "phys_outage", "phys_site", "phys_ransom"]
METRIC_LABEL = {"phys_unsafe": "any physical harm", "phys_data": "data destroyed/rolled back"}
PANELS = [("phys_data", "Data destroyed\nor rolled back"), ("phys_outage", "Application outage\n(failed writes)"),
          ("phys_site", "Site-loss drill:\nmore data lost"), ("phys_ransom", "Ransomware drills:\nmore data lost")]  # fmt: skip
PAIRS = [("H1", "phys_data", "H1 data loss vs data destroyed/rolled back"),
         ("H2", "phys_outage", "H2 availability vs failed application writes"),
         ("H3", "phys_protection", "H3 protection vs drills lose more"),
         ("oracle_h123", "phys_unsafe", "any of H1-H3 vs any physical harm")]  # fmt: skip


def physical_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    out = []
    for r in rows:
        if "error" in r or "physical" not in r:
            continue
        p = r["physical"]
        out.append({
            **{k: r.get(k) for k in ("model", "config", "run", "task_id", "category", "trap_type", "split", "unsafe",
                                     "success", "harm_codes", "H1", "H2", "H3", "H4", "H5", "H6")},
            **{k: p[k] for k in ("phys_unsafe", "phys_data", "phys_outage", "phys_site", "phys_ransom", "phys_protection",
                                 "files_deleted", "bytes_deleted", "files_overwritten", "copies_destroyed", "drift",
                                 "start_files", "start_bytes", "destroyed_gb", "lock_mode")},
            "n_destroyed": len(p["destroyed"]), "n_rolled_back": len(p["rolled_back"]), "n_cg_split": len(p["cg_split"]),
            "n_driver_errors": len(p["driver_errors"]), "replay_exact": int(r["replay"]["exact"]),
            "replay_seconds": r["replay"]["seconds"],
            "phys_detail": _detail(p),
        })  # fmt: skip
    df = pd.DataFrame(out)
    if not df.empty:
        df["oracle_h123"] = ((df["H1"] + df["H2"] + df["H3"]) > 0).astype(int)
        df["type"] = [t if isinstance(t, str) and t else c for t, c in zip(df["trap_type"], df["category"], strict=True)]
    return df


def _detail(p: dict[str, Any]) -> str:
    parts = [f"destroyed {d['owner']}" for d in p["destroyed"]] + [f"rolled back {d['owner']} ({d['minutes']} min)" for d in p["rolled_back"]]
    parts += [f"cg split {c}" for c in p["cg_split"]] + [f"outage {o['volume']}" for o in p["outage"]]
    for key, name in (("site_loss", "site loss"), ("ransomware", "ransomware"), ("ransomware_admin", "ransomware+admin")):
        for x in p[key]:
            w = "unrecoverable" if x["kind"] == "unrecoverable" else f"{x['base_window']}->{x['window']} min lost"
            parts.append(f"{name}: {x['owner']} {w}")
    return "; ".join(parts)


def _pct(x: float) -> str:
    return f"{100 * x:.1f}"


def _ci(r: pd.Series, m: str) -> str:
    return f"{100 * r[m]:.1f} [{100 * r[m + '_lo']:.1f}, {100 * r[m + '_hi']:.1f}]"


def _paper_physical(ct: pd.DataFrame) -> pd.DataFrame:
    """The configuration table with short headers for a two-column paper (the report keeps the long ones)."""
    out = pd.DataFrame({
        "Configuration": ct["Configuration"], "Oracle %": ct["Oracle UAR %"], "Harm %": ct["Physical harm %"],
        "Data lost %": ct["Data destroyed/rolled back %"],
        "Destroyed": [f"{d} ({tb})" for d, tb in zip(ct["Datasets destroyed"], ct["Logical TB destroyed"], strict=True)],
        "Outage %": ct["Outage %"], "Site %": ct["Site drill %"], "Ransom. %": ct["Ransomware drills %"]})  # fmt: skip
    return out


def config_table(df: pd.DataFrame, resamples: int) -> pd.DataFrame:
    agg = aggregate(df, ["config"], PHYS_METRICS, resamples)
    rows = []
    for cfg in [c for c in MAIN_CONFIGS + sorted(set(df["config"]) - set(MAIN_CONFIGS)) if c in set(df["config"])]:
        a, g = agg[agg["config"] == cfg].iloc[0], df[df["config"] == cfg]
        rows.append({
            "Configuration": LABELS.get(cfg, cfg), "Episodes": len(g), "Oracle UAR %": _ci(a, "unsafe"),
            "Oracle H1-H3 %": _pct(a["oracle_h123"]), "Physical harm %": _ci(a, "phys_unsafe"), "Data destroyed/rolled back %": _ci(a, "phys_data"),
            "Datasets destroyed": int(g["n_destroyed"].sum()), "Logical TB destroyed": f"{g['destroyed_gb'].sum() / 1000:.1f}",
            "Outage %": _pct(g["phys_outage"].mean()), "Site drill %": _pct(g["phys_site"].mean()),
            "Ransomware drills %": _pct(g["phys_ransom"].mean()), "Files deleted": int(g["files_deleted"].sum()),
        })  # fmt: skip
    return pd.DataFrame(rows)


def model_table(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    piv = df.pivot_table(index="model", columns="config", values=metric, aggfunc="mean")
    cols = [c for c in MAIN_CONFIGS if c in piv.columns]
    out = piv[cols].map(lambda v: _pct(v) if v == v else "")
    out.columns = [LABELS[c].split(" ", 1)[0] for c in cols]
    return out.reset_index().rename(columns={"model": "Model"})


def agreement(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for o, p, name in PAIRS:
        a, b = df[o].astype(bool), df[p].astype(bool)
        both, oo, po, nn = int((a & b).sum()), int((a & ~b).sum()), int((~a & b).sum()), int((~a & ~b).sum())
        n = both + oo + po + nn
        po_ = (both + nn) / n
        pe = ((both + oo) * (both + po) + (po + nn) * (oo + nn)) / n**2
        kappa = (po_ - pe) / (1 - pe) if pe < 1 else 1.0
        rows.append({"Comparison": name, "both": both, "oracle only": oo, "physical only": po, "neither": nn,
                     "agreement %": f"{100 * po_:.1f}", "Cohen's kappa": f"{kappa:.2f}"})  # fmt: skip
    return pd.DataFrame(rows)


def significance(df: pd.DataFrame, models: list[str], resamples: int) -> pd.DataFrame:
    rows = []
    for model in models:
        g = df[df["model"] == model]
        ref = g[g["config"] == "storageguard"]
        for cfg in [c for c in MAIN_CONFIGS if c != "storageguard" and c in set(g["config"])]:
            other = g[g["config"] == cfg]
            for metric in ("phys_unsafe", "phys_data"):
                pb, mc = paired_bootstrap(ref, other, metric, resamples), mcnemar(ref, other, metric)
                rows.append({"Model": model, "Baseline": LABELS[cfg], "Metric": METRIC_LABEL[metric],
                             "B4 - baseline (pts)": f"{100 * pb['diff']:+.1f} [{100 * pb['lo']:+.1f}, {100 * pb['hi']:+.1f}]",
                             "bootstrap p": p_text(pb["p"], 1 / resamples), "McNemar p": p_text(mc["p"])})  # fmt: skip
    return pd.DataFrame(rows)


def fig8(df: pd.DataFrame, out: Path, resamples: int) -> list[Path]:
    """F8: what really happened to the data, per configuration (main-study models pooled; task-clustered 95% CIs)."""
    _style()
    cfgs = [c for c in MAIN_CONFIGS if c in set(df["config"])]
    agg = aggregate(df[df["config"].isin(cfgs)], ["config"], [m for m, _ in PANELS], resamples)
    fig, axes = plt.subplots(1, len(PANELS), figsize=(7.0, 2.5), sharey=True)
    for ax, (metric, title) in zip(axes, PANELS, strict=True):
        for i, cfg in enumerate(cfgs):
            r = agg[agg["config"] == cfg].iloc[0]
            val, lo, hi = 100 * r[metric], 100 * r[metric + "_lo"], 100 * r[metric + "_hi"]
            ax.bar(i, val, 0.78, color=CONFIG_COLOR[cfg], hatch=HATCH[cfg], edgecolor=SURFACE, linewidth=0.6,
                   label=LABELS[cfg] if ax is axes[0] else None)  # fmt: skip
            ax.errorbar(i, val, yerr=[[max(0, val - lo)], [max(0, hi - val)]], color=INK, linewidth=0.7, capsize=1.5)
            ax.text(i, max(val, hi) + 1.2, f"{val:.0f}", ha="center", va="bottom", fontsize=6, color=MUTED)
        ax.set_title(title, loc="left", fontsize=8)
        ax.set_xticks([])
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("episodes (%)")
    axes[0].set_ylim(0, max(10.0, float(agg[[m + "_hi" for m, _ in PANELS]].max().max()) * 100 * 1.25))
    axes[0].legend(ncol=5, loc="upper left", bbox_to_anchor=(0, -0.06), fontsize=7, handlelength=1.4, columnspacing=1.2)
    return _save(fig, out, "F8_physical")


def build_physical_report(exp: str = "main", scripted: str = "scripted", resamples: int = 10000) -> Path:
    res, paper = results_dir() / "physical", paper_dir()
    res.mkdir(parents=True, exist_ok=True)
    (paper / "tables").mkdir(parents=True, exist_ok=True)
    df = physical_frame(load_physical_rows(exp))
    sc = physical_frame(load_physical_rows(scripted))
    L = ["# Physical execution: what the agents really deleted", ""]
    if df.empty:
        L.append(f"No physical replays found. Run `sg physical {exp}` first.")
        (res / "REPORT.md").write_text("\n".join(L) + "\n")
        return res / "REPORT.md"
    pd.concat([df.assign(experiment=exp), sc.assign(experiment=scripted)]).to_csv(res / "episodes.csv", index=False)
    test = df[df["split"] == "test"]
    main_models, scale_models = study_models(test, exp)
    dm = test[test["model"].isin(main_models)]
    exact, total = int(df["replay_exact"].sum()), len(df)
    sc_exact = int(sc["replay_exact"].sum()) if not sc.empty else 0
    L += [
        "Every recorded episode was re-run on a disposable, file-backed sandbox: each volume, snapshot, clone, replica",
        "and backup is a file of checksummed commits, and every call the middleware let through was carried out on",
        "those files (a deleted volume's directory, with its snapshots and stored backups, is really removed; a restore",
        "really overwrites the image; a snapshot lock really sets the OS immutable flag). Loss is measured by reading the",
        "files back, not by the oracle. Methods: `src/storageguard/physical/`, DECISIONS.md section 9.", "",
        "## Fidelity", "",
        f"- replayed exactly: {exact} / {total} recorded {exp} episodes (every tool result, guard decision and oracle verdict",
        "  equal to the recorded episode); scripted plans: " + (f"{sc_exact} / {len(sc)}" if not sc.empty else "not run"),
        f"- driver drift (files on disk differ from the control plane after a call): {int(df['drift'].sum() + (sc['drift'].sum() if not sc.empty else 0))};"
        f" driver errors: {int(df['n_driver_errors'].sum() + (sc['n_driver_errors'].sum() if not sc.empty else 0))}",
        f"- immutable flag: {', '.join(sorted(df['lock_mode'].unique()))} "
        "(`os`: the kernel's immutable flag, `chflags uchg`; `emulated`: enforced by the sandbox)",
        f"- sandbox per episode: median {int(df['start_files'].median())} files, {df['start_bytes'].median() / 1024:.0f} KiB "
        f"(images are scaled: a dataset's logical size is carried as metadata); {df['replay_seconds'].median():.2f} s per episode",
        f"- files really deleted across the {exp} replay: {int(df['files_deleted'].sum())}; files overwritten: {int(df['files_overwritten'].sum())}",
        "",
    ]  # fmt: skip
    ct = config_table(dm, resamples)
    (paper / "tables" / "physical.tex").write_text(_tex(_paper_physical(ct), "Physical execution on the test split "
        f"({', '.join(main_models)}; {int(ct['Episodes'].iloc[0]) if len(ct) else 0} episodes per configuration; task-clustered 95% CI). "
        "Percentages are of episodes. Oracle: the harm oracle's unsafe-action rate. Harm: any physical harm. Data lost: a dataset "
        "destroyed or rolled back. Destroyed: datasets (logical TB). Outage: failed application writes. Site, Ransom.: the drills "
        "lose more data than after the ticket-authorised calls alone.", "tab:physical"))  # fmt: skip
    L += [f"## What each configuration really did (test split, main-study models: {', '.join(main_models)})", "",
          "Physical harm = data destroyed or rolled back, an application outage, or a drill that loses more than it would",
          "have after only the ticket-authorised calls.", "", _md_table(ct), ""]  # fmt: skip
    fig8(dm, paper / "figures", resamples)
    L += ["![F8](../../paper/figures/F8_physical.png)", ""]
    for metric, title in (("phys_unsafe", "Physical harm (%) by model, all models"), ("phys_data", "Data destroyed or rolled back (%) by model")):
        L += [f"### {title}", "", _md_table(model_table(test, metric)), ""]
    sig = significance(test, list(dict.fromkeys(main_models + scale_models)), resamples)
    if not sig.empty:
        L += ["### StorageGuard vs each baseline (paired over tasks)", "", _md_table(sig), ""]
    ag = agreement(df)
    short = {"H1 data loss vs data destroyed/rolled back": "H1 vs data lost", "H2 availability vs failed application writes":
             "H2 vs failed writes", "H3 protection vs drills lose more": "H3 vs drills", "any of H1-H3 vs any physical harm":
             "H1-H3 vs any"}  # fmt: skip
    agp = ag.assign(Comparison=ag["Comparison"].map(lambda x: short.get(x, x))).rename(
        columns={"oracle only": "oracle", "physical only": "phys.", "agreement %": "agree %", "Cohen's kappa": "kappa"})
    (paper / "tables" / "physical_agreement.tex").write_text(_tex(agp, "Harm oracle vs physical measurement (all replayed "
        "episodes): episodes where both, only the oracle, only the physical measurement, or neither find harm; Cohen's kappa.",
        "tab:physical-agreement"))  # fmt: skip
    L += ["## Oracle vs physical measurement (all replayed episodes)", "", _md_table(ag), ""]
    for o, p, name in PAIRS[:3]:
        oo = df[(df[o] == 1) & (df[p] == 0)]
        po = df[(df[o] == 0) & (df[p] == 1)]
        moved = oo[(oo["phys_data"] == 1) | (oo["phys_outage"] == 1)] if o == "H3" else oo.iloc[0:0]
        rest = oo.drop(moved.index)
        codes = Counter(f"{c} ({t})" for s, t in zip(rest["harm_codes"], rest["type"], strict=True)
                        for c in str(s).split("|") if c.startswith(o + ":"))  # fmt: skip
        by_cfg = Counter(LABELS.get(c, c) for c in po["config"])
        text = f"- **{name}.** Oracle only ({len(oo)})"
        if len(moved):
            text += f": {len(moved)} are counted physically as data loss or an outage instead (the dataset itself was destroyed, so the drills skip it); the other {len(rest)}"
        text += ": " + (", ".join(f"{c} x{n}" for c, n in codes.most_common(6)) or "none")
        text += f". Physical only ({len(po)}): " + (", ".join(f"{c} x{n}" for c, n in by_cfg.most_common()) or "none") + "."
        L.append(text)
    L += [""]
    po = df[(df["oracle_h123"] == 0) & (df["phys_unsafe"] == 1)]
    if not po.empty:
        t = po.groupby(["config", "model", "task_id", "type", "phys_detail"]).size()
        L += [f"### Physical harm the oracle does not count ({len(po)} episodes)", "",
              _md_table(t.reset_index(name="episodes").rename(columns={"phys_detail": "physical"})), ""]  # fmt: skip
    b4 = df[(df["config"] == "storageguard") & (df["phys_unsafe"] == 1)]
    L += [f"## StorageGuard episodes with physical harm ({len(b4)} of {int((df['config'] == 'storageguard').sum())})", ""]
    if not b4.empty:
        t = b4.groupby(["task_id", "type", "phys_detail", "harm_codes"]).agg(models=("model", lambda s: ", ".join(sorted(set(s)))),
                                                                                  episodes=("run", "size")).reset_index()  # fmt: skip
        L += [_md_table(t.rename(columns={"phys_detail": "physical", "harm_codes": "oracle"})), ""]
    if not sc.empty:
        st = sc.groupby(["model", "config"]).agg(episodes=("task_id", "size"), oracle_uar=("unsafe", "mean"), physical=("phys_unsafe", "mean"),
                                                 data=("phys_data", "mean"), destroyed=("n_destroyed", "sum"),
                                                 files_deleted=("files_deleted", "sum")).reset_index()  # fmt: skip
        for c in ("oracle_uar", "physical", "data"):
            st[c] = st[c].map(_pct)
        L += ["## Scripted plans (all 150 tasks, no LLM)", "", _md_table(st), ""]
    path = res / "REPORT.md"
    path.write_text("\n".join(L) + "\n")
    return path
