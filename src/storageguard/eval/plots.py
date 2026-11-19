"""Paper figures F1-F7 (matplotlib, PDF + PNG).

Colour: a fixed categorical order validated for colour-vision deficiency
(one hue per configuration, never cycled), one-hue sequential ramp for
magnitude, hatches as a secondary encoding so figures survive greyscale print.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Figures are files only. A notebook (Colab, Jupyter) exports MPLBACKEND=module://matplotlib_inline...,
# which matplotlib >= 3.11 rejects at import when that module is not installed in this environment.
os.environ["MPLBACKEND"] = "Agg"
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.lines  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from .baselines import LABELS, parse_config  # noqa: E402
from .stats import aggregate  # noqa: E402

INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e1e0d9", "#ffffff"
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
CONFIG_ORDER = ["noguard", "prompt_only", "rbac", "llm_judge", "storageguard",
                "a1_one_hop", "a2_no_reversibility", "a3_no_rewrites", "a4_no_feedback"]  # fmt: skip
CONFIG_COLOR = {c: CATEGORICAL[i] for i, c in enumerate(["noguard", "prompt_only", "rbac", "llm_judge", "storageguard"])}
CONFIG_COLOR.update({"a1_one_hop": CATEGORICAL[5], "a2_no_reversibility": CATEGORICAL[6], "a3_no_rewrites": CATEGORICAL[7],
                     "a4_no_feedback": "#898781"})  # fmt: skip
HATCH = {"noguard": "", "prompt_only": "//", "rbac": "..", "llm_judge": "xx", "storageguard": "",
         "a1_one_hop": "\\\\", "a2_no_reversibility": "++", "a3_no_rewrites": "--", "a4_no_feedback": "oo"}  # fmt: skip
SEQ = LinearSegmentedColormap.from_list("sg_blue", ["#f3f7fd", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
TRAPS = [f"T{i}" for i in range(1, 13)]
MARKER = {"noguard": "D", "prompt_only": "v", "rbac": "s", "llm_judge": "^", "storageguard": "o"}


def model_meta(name: str) -> tuple[str, float | None, str, int]:
    """(family, size in billions, weight format, bits) from a results name such as ``qwen2.5-32b-int8``.

    Names without a quantisation tag are the official BF16 weights served with vLLM (DECISIONS.md, C1);
    ``-q4`` marks the 4-bit Ollama fallback (C6).
    """
    m = re.search(r"-(\d+(?:\.\d+)?)b(?=-|$)", name)
    family, size = (name[: m.start()], float(m.group(1))) if m else (name, None)
    low = name.lower()
    if "awq" in low:
        return family, size, "4-bit (AWQ)", 4
    if low.endswith("-q4"):
        return family, size, "4-bit (GGUF)", 4
    if "int8" in low:
        return family, size, "8-bit (GPTQ)", 8
    return family, size, "BF16", 16


def _style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8,
        "axes.edgecolor": "#c3c2b7", "axes.labelcolor": INK, "text.color": INK, "xtick.color": MUTED, "ytick.color": MUTED,
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.axisbelow": True, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.bbox": "tight",
        "savefig.dpi": 300, "legend.frameon": False, "pdf.fonttype": 42, "hatch.linewidth": 0.5,
    })  # fmt: skip


def _save(fig: plt.Figure, out: Path, name: str) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / f"{name}.pdf", out / f"{name}.png"]
    for p in paths:
        fig.savefig(p)
    plt.close(fig)
    return paths


def _label(cfg: str) -> str:
    try:
        return parse_config(cfg).label
    except ValueError:
        return cfg


def _base(cfg: str) -> str:
    return cfg.split("@")[0]


def _order(cfgs: list[str]) -> list[str]:
    return sorted(cfgs, key=lambda c: (CONFIG_ORDER.index(_base(c)) if _base(c) in CONFIG_ORDER else 99, c))


def _empty(out: Path, name: str, why: str) -> list[Path]:
    fig, ax = plt.subplots(figsize=(3.4, 1.2))
    ax.axis("off")
    ax.text(0.5, 0.5, f"{name}: {why}", ha="center", va="center", color=MUTED)
    return _save(fig, out, name)


def _bars(ax: plt.Axes, agg: pd.DataFrame, metric: str, cfgs: list[str], models: list[str], pct: bool = True) -> None:
    """Grouped bars: one group per model, one (fixed-colour) bar per configuration, 95% CI whiskers."""
    width = 0.8 / max(1, len(cfgs))
    scale = 100 if pct else 1
    for j, cfg in enumerate(cfgs):
        for i, model in enumerate(models):
            row = agg[(agg["model"] == model) & (agg["config"] == cfg)]
            if row.empty:
                continue
            r = row.iloc[0]
            x = i + (j - (len(cfgs) - 1) / 2) * width
            val = r[metric] * scale
            ax.bar(x, val, width * 0.86, color=CONFIG_COLOR.get(_base(cfg), "#898781"), hatch=HATCH.get(_base(cfg), ""),
                   edgecolor=SURFACE, linewidth=0.6, label=_label(cfg) if i == 0 else None)  # fmt: skip
            ax.errorbar(x, val, yerr=[[max(0, val - r[f"{metric}_lo"] * scale)], [max(0, r[f"{metric}_hi"] * scale - val)]],
                        color=INK, linewidth=0.7, capsize=1.5)  # fmt: skip
            if len(cfgs) * len(models) <= 12:  # value label sits above the whisker cap, never on it
                top = max(val, r[f"{metric}_hi"] * scale)
                ax.text(x, top + (1.5 if pct else 0.02), f"{val:.0f}" if pct else f"{val:.2f}", ha="center", va="bottom", fontsize=6, color=MUTED)
    ax.set_xticks(range(len(models)), models)
    ax.grid(axis="x", visible=False)
    if pct:
        ax.set_ylim(0, 112)
    else:
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi + 0.08 * (hi - lo))  # headroom for the value labels


def fig1_heatmap(df: pd.DataFrame, out: Path) -> list[Path]:
    """F1: Unsafe Action Rate by trap type x model with no guard."""
    d = df[(df["config"] == "noguard") & (df["category"].isin(["trap", "injection"]))]
    if d.empty:
        return _empty(out, "F1_unsafe_heatmap", "no B0 data")
    types = [t for t in [*TRAPS, "P1", "P2", "P3"] if t in set(d["trap_type"])]
    models = sorted(d["model"].unique())
    mat = np.array([[d[(d["model"] == m) & (d["trap_type"] == t)]["unsafe"].mean() * 100 for t in types] for m in models])
    fig, ax = plt.subplots(figsize=(7.0, 0.55 * len(models) + 1.1))
    im = ax.imshow(mat, cmap=SEQ, vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(len(types)), types)
    ax.set_yticks(range(len(models)), models)
    ax.grid(False)
    for i in range(len(models)):
        for j in range(len(types)):
            if not np.isnan(mat[i, j]):
                ax.text(j, i, f"{mat[i, j]:.0f}", ha="center", va="center", fontsize=7, color="#ffffff" if mat[i, j] > 55 else INK)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks(np.arange(-0.5, len(types)), minor=True)
    ax.set_yticks(np.arange(-0.5, len(models)), minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)
    ax.tick_params(which="both", length=0)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cb.set_label("Unsafe Action Rate (%)")
    cb.outline.set_visible(False)
    ax.set_title("Unsafe Action Rate without a guard (B0), by trap / injection type", loc="left")
    return _save(fig, out, "F1_unsafe_heatmap")


def fig2_main(df: pd.DataFrame, out: Path, resamples: int) -> list[Path]:
    """F2: Unsafe Action Rate and Task Success Rate per configuration (small multiples, one axis each)."""
    d = df[df["config"].isin(["noguard", "prompt_only", "rbac", "llm_judge", "storageguard"])]
    if d.empty:
        return _empty(out, "F2_uar_tsr", "no data")
    agg = aggregate(d, ["model", "config"], ["unsafe", "success"], resamples)
    cfgs, models = _order(list(d["config"].unique())), sorted(d["model"].unique())
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.5), sharey=True)
    for ax, metric, title in zip(axes, ("unsafe", "success"), ("Unsafe Action Rate (%)  lower is better", "Task Success Rate (%)  higher is better"), strict=True):
        _bars(ax, agg, metric, cfgs, models)
        ax.set_title(title, loc="left")
    axes[0].legend(ncol=5, loc="upper left", bbox_to_anchor=(0, -0.14), fontsize=7, handlelength=1.4, columnspacing=1.2)
    return _save(fig, out, "F2_uar_tsr")


def fig3_pareto(df: pd.DataFrame, out: Path, sweep_ref: pd.DataFrame | None = None) -> list[Path]:
    """F3: safety/friction trade-off, one panel per model: Unsafe Action Rate vs escalations per task.

    A model with a threshold sweep is drawn from ``sweep_ref`` (sweep episodes plus the main run's episodes with the
    same run indices), so every point of that panel rests on the same seeds; other models use all main-run episodes.
    """

    def keep(x: pd.DataFrame) -> pd.DataFrame:
        return x[x["config"].map(_base).isin(["storageguard", "rbac", "noguard", "llm_judge"]) & ~x["config"].str.contains("human=")]

    d = keep(df)
    if d.empty:
        return _empty(out, "F3_pareto", "no data")
    ref = keep(sweep_ref) if sweep_ref is not None and not sweep_ref.empty else d.iloc[0:0]
    models = sorted(d["model"].unique())
    ncols = min(4, len(models))
    nrows = -(-len(models) // ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.5 if ncols == 1 else 7.0, 2.5 * nrows), sharey=True, squeeze=False, layout="constrained")
    for ax, model in zip(axes.flat, models, strict=False):
        swept = ref[(ref["model"] == model) & ref["config"].str.contains("tau_")]
        g = ref[ref["model"] == model] if len(swept) else d[d["model"] == model]
        pts = g.groupby("config").agg(unsafe=("unsafe", "mean"), esc=("n_escalations", "mean")).reset_index()
        sweep = pts[pts["config"].map(_base) == "storageguard"].sort_values("esc")
        if len(sweep) > 1:
            ax.plot(sweep["esc"], sweep["unsafe"] * 100, color=CONFIG_COLOR["storageguard"], linewidth=1.2, zorder=2)
        labelled: list[tuple[float, float]] = []
        for _, r in pts.sort_values("config", key=lambda c: c.str.contains("@")).iterrows():  # main configs first, sweep after
            base, swept_pt = _base(r["config"]), "@" in r["config"]
            xy = (float(r["esc"]), float(r["unsafe"]) * 100)
            ax.scatter(*xy, s=18 if swept_pt else 34, color=SURFACE if swept_pt else CONFIG_COLOR.get(base, "#898781"),
                       edgecolor=CONFIG_COLOR.get(base, "#898781") if swept_pt else SURFACE, linewidth=1.2,
                       marker=MARKER.get(base, "o"), zorder=2 if swept_pt else 3)  # fmt: skip
            if any(abs(xy[0] - a) < 1e-9 and abs(xy[1] - b) < 1e-9 for a, b in labelled):
                continue  # a sweep point identical to an already-labelled point (e.g. the default thresholds) gets no second label
            labelled.append(xy)
            tag = r["config"].split("@")[1].replace("tau_allow=", "τa=").replace("tau_block=", "τb=").replace(",", " ") if swept_pt else LABELS.get(base, base)
            # the full guard's label: up-left, clear of the sweep labels on the right, in a panel with a sweep;
            # otherwise up-right like the others, so it never runs into the y-axis title
            guard_pt = base == "storageguard" and not swept_pt
            left = guard_pt and len(sweep) > 1 and xy[0] > 0.2 * float(pts["esc"].max())
            offset = (-5, 4) if left else (5, 3)
            ax.annotate(tag, xy, textcoords="offset points", xytext=offset, ha="right" if left else "left",
                        fontsize=5.5 if swept_pt else 6, color=MUTED)  # fmt: skip
        ax.set_title(model + (f" (run {', '.join(str(x) for x in sorted(swept['run'].unique()))}, threshold sweep)" if len(swept) else ""),
                     loc="left", fontsize=8)  # fmt: skip
        ax.set_xlim(left=-0.08)
        ax.set_xlim(right=ax.get_xlim()[1] * 1.25)  # room for the labels of the right-most points
    for ax in axes.flat[len(models):]:
        ax.set_visible(False)
    for ax in axes[-1]:
        ax.set_xlabel("Escalations per task")
    for row in axes:
        row[0].set_ylabel("Unsafe Action Rate (%)")
    axes[0][0].set_ylim(bottom=-2)
    fig.suptitle("Safety vs human workload", x=0.01, ha="left", fontsize=9)
    return _save(fig, out, "F3_pareto")


def fig4_ablations(df: pd.DataFrame, out: Path, resamples: int) -> list[Path]:
    """F4: ablations A1-A4 against the full guard (three small multiples)."""
    keep = ["storageguard", "a1_one_hop", "a2_no_reversibility", "a3_no_rewrites", "a4_no_feedback"]
    d = df[df["config"].isin(keep)]
    if d["config"].nunique() < 2:
        return _empty(out, "F4_ablations", "no ablation data")
    model = sorted(d["model"].unique())[0]
    models_with_all = [m for m in d["model"].unique() if d[d["model"] == m]["config"].nunique() > 1]
    model = models_with_all[0] if models_with_all else model
    d = d[d["model"] == model]
    agg = aggregate(d, ["model", "config"], ["unsafe", "success", "n_escalations"], resamples)
    cfgs = _order(list(d["config"].unique()))
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.5), layout="constrained")
    for ax, metric, title, pct in zip(axes, ("unsafe", "success", "n_escalations"),
                                      ("Unsafe Action Rate (%)", "Task Success Rate (%)", "Escalations / task"), (True, True, False), strict=True):  # fmt: skip
        _bars(ax, agg, metric, cfgs, [model], pct)
        ax.set_title(title, loc="left")
        ax.set_xticks([])
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=5, loc="outside lower center", fontsize=7, handlelength=1.4, columnspacing=1.2)
    fig.suptitle(f"Guard ablations ({model})", x=0.01, ha="left", fontsize=9)
    return _save(fig, out, "F4_ablations")


def fig5_injection(df: pd.DataFrame, out: Path, resamples: int) -> list[Path]:
    """F5: robustness to prompt injection hidden in storage metadata."""
    d = df[(df["category"] == "injection") & df["config"].isin(["noguard", "prompt_only", "rbac", "llm_judge", "storageguard"])]
    if d.empty:
        return _empty(out, "F5_injection", "no injection data")
    agg = aggregate(d, ["model", "config"], ["unsafe", "success"], resamples)
    cfgs, models = _order(list(d["config"].unique())), sorted(d["model"].unique())
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.4), sharey=True)
    _bars(axes[0], agg, "unsafe", cfgs, models)
    axes[0].set_title("Injection tasks: Unsafe Action Rate (%)", loc="left")
    _bars(axes[1], agg, "success", cfgs, models)
    axes[1].set_title("Injection tasks: Task Success Rate (%)", loc="left")
    axes[0].legend(ncol=5, loc="upper left", bbox_to_anchor=(0, -0.14), fontsize=7, handlelength=1.4, columnspacing=1.2)
    return _save(fig, out, "F5_injection")


def fig6_overhead(df: pd.DataFrame, events: pd.DataFrame, out: Path) -> list[Path]:
    """F6: overhead: guard decision latency, and steps / tokens per task by configuration."""
    d = df[df["config"].isin(["noguard", "prompt_only", "rbac", "llm_judge", "storageguard"])]
    if d.empty:
        return _empty(out, "F6_overhead", "no data")
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.3), layout="constrained", gridspec_kw={"width_ratios": [1.15, 1, 0.8]})
    ax = axes[0]
    lat = events[events["config"] == "storageguard"]["latency_ms"].dropna() if not events.empty else pd.Series(dtype=float)
    if len(lat):
        ax.hist(lat, bins=np.logspace(np.log10(max(0.3, lat.min())), np.log10(lat.max() + 1), 28), color=CONFIG_COLOR["storageguard"],
                edgecolor=SURFACE, linewidth=0.5)  # fmt: skip
        ax.set_xscale("log")
        ticks = [t for t in (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500) if lat.min() / 1.5 <= t <= max(60, lat.max() * 1.5)]
        ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        med = float(lat.median())
        ax.axvline(med, color=INK, linewidth=1)
        ax.text(med, ax.get_ylim()[1] * 0.92, f" median {med:.1f} ms", fontsize=7, color=INK)
        ax.axvline(50, color=MUTED, linewidth=0.8, linestyle="--")
        ax.text(50, ax.get_ylim()[1] * 0.75, " 50 ms target", fontsize=6, color=MUTED)
    ax.set_title("StorageGuard decision latency", loc="left")
    ax.set_xlabel("ms per mutating call (log)")
    ax.set_ylabel("calls")
    cfgs = _order(list(d["config"].unique()))
    means = d.groupby("config")[["steps", "tokens"]].mean()
    for ax, col, title, div in ((axes[1], "steps", "Agent steps per task", 1), (axes[2], "tokens", "Tokens per task (thousands)", 1000)):
        vals = [means.loc[c, col] / div for c in cfgs]
        ax.barh(range(len(cfgs)), vals, 0.62, color=[CONFIG_COLOR[_base(c)] for c in cfgs], edgecolor=SURFACE)
        for i, v in enumerate(vals):
            ax.text(v, i, f" {v:.1f}", va="center", fontsize=7, color=MUTED)
        ax.set_yticks(range(len(cfgs)), [_label(c) for c in cfgs] if col == "steps" else [""] * len(cfgs))
        if col != "steps":
            ax.tick_params(axis="y", length=0)
        ax.invert_yaxis()
        ax.grid(axis="y", visible=False)
        ax.set_xlim(0, (max(vals) * 1.22) or 1.0)  # scripted plans use no tokens: avoid a zero-width axis
        ax.set_title(title, loc="left")
    return _save(fig, out, "F6_overhead")


def fig7_scale(df: pd.DataFrame, out: Path, resamples: int, models: list[str]) -> list[Path]:
    """F7: Unsafe Action Rate and Task Success Rate across model size within one family.

    For each size the least compressed model is the ladder point (connected); any other model of the same size
    (e.g. 4-bit weights of the 7B model) is drawn hollow beside it as a quantisation control.
    """
    d = df[df["model"].isin(models) & df["config"].isin(["noguard", "prompt_only", "rbac", "llm_judge", "storageguard"])]
    meta = {m: model_meta(m) for m in d["model"].unique() if model_meta(m)[1] is not None}
    if len(meta) < 2:
        return _empty(out, "F7_scale", "no scale study")
    best: dict[float, str] = {}
    for m, (_, size, _, bits) in meta.items():
        if size not in best or bits > meta[best[size]][3]:
            best[size] = m  # type: ignore[index]
    ladder = sorted(best.values(), key=lambda m: meta[m][1] or 0)
    controls = [m for m in meta if m not in ladder]
    agg = aggregate(d[d["model"].isin(meta)], ["model", "config"], ["unsafe", "success"], resamples).set_index(["config", "model"])
    cfgs = _order(list(d["config"].unique()))
    sizes = [meta[m][1] for m in ladder]
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.7), sharey=True, layout="constrained")
    for ax, metric, title in zip(axes, ("unsafe", "success"), ("Unsafe Action Rate (%)", "Task Success Rate (%)"), strict=True):
        for j, cfg in enumerate(cfgs):
            jit = 1 + 0.035 * (j - (len(cfgs) - 1) / 2)  # small horizontal offset so the whiskers do not overlap
            pts = [m for m in ladder if (cfg, m) in agg.index]
            ys = np.array([agg.loc[(cfg, m), metric] * 100 for m in pts])
            err = [ys - [agg.loc[(cfg, m), f"{metric}_lo"] * 100 for m in pts], [agg.loc[(cfg, m), f"{metric}_hi"] * 100 for m in pts] - ys]
            ax.errorbar([meta[m][1] * jit for m in pts], ys, yerr=np.clip(err, 0, None), color=CONFIG_COLOR[cfg], marker=MARKER[cfg],
                        markersize=4, linewidth=1.4, elinewidth=0.6, capsize=1.5, label=_label(cfg))  # fmt: skip
            for m in controls:
                if (cfg, m) in agg.index:
                    ax.scatter(meta[m][1] * 1.22 * jit, agg.loc[(cfg, m), metric] * 100, s=20, marker=MARKER[cfg], facecolor=SURFACE,
                               edgecolor=CONFIG_COLOR[cfg], linewidth=1.1, zorder=3)  # fmt: skip
        ax.set_xscale("log")
        ax.set_xticks(sizes, [f"{s:g}B\n{meta[m][2].split()[0]}" for s, m in zip(sizes, ladder, strict=True)])
        ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        ax.set_xlim(sizes[0] / 1.3, sizes[-1] * 1.3)
        ax.set_ylim(-3, 103)
        ax.grid(axis="x", visible=False)
        ax.set_title(title, loc="left")
    handles, labels = axes[0].get_legend_handles_labels()
    if controls:
        handles.append(matplotlib.lines.Line2D([], [], linestyle="", marker="o", markerfacecolor=SURFACE, markeredgecolor=MUTED))
        labels.append("hollow: " + "; ".join(f"{meta[m][1]:g}B, {meta[m][2].replace(' (', ' ').rstrip(')')} weights" for m in controls)
                      + " (quantisation control)")  # fmt: skip
    fig.legend(handles, labels, ncol=3, loc="outside lower center", fontsize=7, handlelength=1.6, columnspacing=1.2)
    fig.suptitle(f"Scale study: {meta[ladder[0]][0]} from {sizes[0]:g}B to {sizes[-1]:g}B", x=0.01, ha="left", fontsize=9)
    return _save(fig, out, "F7_scale")


def make_figures(main: pd.DataFrame, extra: pd.DataFrame, events: pd.DataFrame, out: Path, resamples: int = 10000,
                 extra_ref: pd.DataFrame | None = None, main_models: list[str] | None = None,
                 scale_models: list[str] | None = None) -> list[Path]:  # fmt: skip
    """Render the figures.  ``extra`` holds ablation/sweep episodes; ``extra_ref`` the same plus the main run's
    episodes with matching run indices (``report.ablation_reference``), so ablations are compared on identical seeds.
    F2, F3, F5 and F6 show the main-study models; F1 every model; F7 the scale study (``report.study_models``)."""
    _style()
    if extra_ref is None:
        extra_ref = pd.concat([main, extra], ignore_index=True) if not extra.empty else main
    dm = main[main["model"].isin(main_models)] if main_models else main
    paths: list[Path] = []
    paths += fig1_heatmap(main, out)
    paths += fig2_main(dm, out, resamples)
    paths += fig3_pareto(dm, out, extra_ref if not extra.empty else None)
    paths += fig4_ablations(extra_ref if not extra.empty else main, out, resamples)
    paths += fig5_injection(dm, out, resamples)
    paths += fig6_overhead(dm, events, out)
    if scale_models:
        paths += fig7_scale(main, out, resamples, scale_models)
    return paths
