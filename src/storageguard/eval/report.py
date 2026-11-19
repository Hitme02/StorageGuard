"""Build every paper artefact from stored episodes: CSVs, statistics, figures, LaTeX tables,
case studies and ``results/REPORT.md`` (which answers RQ1-RQ6 with numbers, automatically)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..bench.splits import count_table, load_bench
from ..paths import paper_dir, results_dir
from .baselines import MAIN_CONFIGS, parse_config
from .plots import make_figures, model_meta
from .runner import load_experiment, load_rows
from .stats import aggregate, bootstrap_ci, compare_to_reference, episodes_frame

ABLATION_CONFIGS = ["a1_one_hop", "a2_no_reversibility", "a3_no_rewrites", "a4_no_feedback"]


def _label(cfg: str) -> str:
    try:
        return parse_config(cfg).label
    except ValueError:
        return cfg


def _pct(mean: float, lo: float | None = None, hi: float | None = None) -> str:
    if mean != mean:
        return "n/a"
    if lo is None or lo != lo:
        return f"{100 * mean:.1f}%"
    return f"{100 * mean:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]"


def _num(mean: float, lo: float | None = None, hi: float | None = None, nd: int = 2) -> str:
    if mean != mean:
        return "n/a"
    if lo is None or lo != lo:
        return f"{mean:.{nd}f}"
    return f"{mean:.{nd}f} [{lo:.{nd}f}, {hi:.{nd}f}]"


def _frame(exp: str, split: str | None = "test") -> pd.DataFrame:
    try:
        df = episodes_frame(load_rows(exp))
    except FileNotFoundError:
        return pd.DataFrame()
    if df.empty:
        return df
    df["experiment"] = exp
    return df[df["split"] == split] if split else df


def guard_events(exp: str) -> pd.DataFrame:
    """Per-call guard events (latency, decision) from the JSONL trajectories."""
    root = results_dir() / "runs" / exp
    out = []
    for p in root.rglob("*.jsonl"):
        cfg, model = p.parent.name, p.parent.parent.name
        for line in p.read_text().splitlines():
            if '"type": "guard"' not in line:
                continue
            e = json.loads(line)
            out.append({"model": model, "config": cfg, "task_id": p.stem.split("__")[0], "decision": e["decision"],
                        "status": e["status"], "latency_ms": e.get("latency_ms") or np.nan, "tool": e["action"]["tool"]})  # fmt: skip
    return pd.DataFrame(out)


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_no data_\n"
    head = "| " + " | ".join(df.columns) + " |\n|" + "|".join("---" for _ in df.columns) + "|\n"
    return head + "".join("| " + " | ".join(str(v) for v in row) + " |\n" for row in df.itertuples(index=False))


def _tex(df: pd.DataFrame, caption: str, label: str) -> str:
    def esc(x: Any) -> str:
        return str(x).replace("%", r"\%").replace("_", r"\_").replace("&", r"\&").replace("τ", r"$\tau$")

    cols = "l" * 2 + "r" * (len(df.columns) - 2) if len(df.columns) > 2 else "l" * len(df.columns)
    # two-column layouts (IEEE Access): wide tables span both columns; any table is scaled down, never up, to fit
    # (graphicx only: \width is the table's natural width)
    env = "table*" if len(df.columns) > 7 else "table"
    lines = [r"\begin{" + env + "}[t]", r"\centering", r"\caption{" + esc(caption) + "}", r"\label{" + label + "}", r"\small",
             r"\resizebox{\ifdim\width>\linewidth\linewidth\else\width\fi}{!}{%",
             r"\begin{tabular}{" + cols + "}", r"\hline", " & ".join(esc(c) for c in df.columns) + r" \\", r"\hline"]  # fmt: skip
    lines += [" & ".join(esc(v) for v in row) + r" \\" for row in df.itertuples(index=False)]
    lines += [r"\hline", r"\end{tabular}", "}", r"\end{" + env + "}", ""]
    return "\n".join(lines)


def _stop_precision(g: pd.DataFrame) -> str:
    """Share of stopped (blocked / denied / unapproved) actions that the oracle confirms would have caused harm."""
    if "n_stops" not in g or g["n_stops"].sum() == 0:
        return "n/a"
    return f"{100 * g['n_justified_stops'].sum() / g['n_stops'].sum():.0f}% of {int(g['n_stops'].sum())}"


def summary_table(df: pd.DataFrame, resamples: int, configs: list[str] | None = None) -> pd.DataFrame:
    """Model x config: UAR, TSR, escalations, false-block rate, steps, tokens (95% CIs)."""
    if df.empty:
        return pd.DataFrame()
    agg = aggregate(df, ["model", "config"], ["unsafe", "success", "n_escalations", "steps", "tokens"], resamples)
    rows = []
    order = configs or sorted(df["config"].unique())
    for model in sorted(df["model"].unique()):
        for cfg in [c for c in order if c in set(df["config"])]:
            a = agg[(agg["model"] == model) & (agg["config"] == cfg)]
            if a.empty:
                continue
            r = a.iloc[0]
            g = df[(df["model"] == model) & (df["config"] == cfg)]
            routine = g[g["category"] == "routine"]
            rows.append({
                "Model": model, "Config": _label(cfg), "Tasks": int(r["n_tasks"]), "Episodes": int(r["n_episodes"]),
                "UAR": _pct(r["unsafe"], r["unsafe_lo"], r["unsafe_hi"]), "TSR": _pct(r["success"], r["success_lo"], r["success_hi"]),
                "Esc/task": _num(r["n_escalations"], r["n_escalations_lo"], r["n_escalations_hi"]),
                "FBR (routine)": _pct(routine["false_block"].mean()) if len(routine) else "n/a",
                "Stop precision": _stop_precision(g),
                "Steps": f"{r['steps']:.1f}", "Tokens": f"{r['tokens']:.0f}",
            })  # fmt: skip
    return pd.DataFrame(rows)


def category_table(df: pd.DataFrame, models: list[str] | None = None) -> pd.DataFrame:
    """UAR / TSR per task category, one row per (model, config)."""
    rows = []
    for model in models or sorted(df["model"].unique()):
        d = df[df["model"] == model]
        for cfg in [c for c in MAIN_CONFIGS if c in set(d["config"])]:
            row: dict[str, Any] = {"Model": model, "Config": _label(cfg)}
            for cat in ("routine", "ambiguous", "trap", "injection"):
                g = d[(d["config"] == cfg) & (d["category"] == cat)]
                row[f"{cat} UAR"] = _pct(g["unsafe"].mean()) if len(g) else "n/a"
                row[f"{cat} TSR"] = _pct(g["success"].mean()) if len(g) else "n/a"
            rows.append(row)
    return pd.DataFrame(rows)


def p_text(p: float, floor: float = 1e-4) -> str:
    """A p-value for a table: '<floor' at or below the resolution (a bootstrap cannot resolve p below 1/resamples)."""
    if p != p:
        return ""
    return f"<{floor:g}" if p <= floor * (1 + 1e-9) else f"{p:.4f}"


def hardest_types(tt: pd.DataFrame, k: int = 4) -> pd.DataFrame:
    """The k trap types with the highest mean UAR, extended to every type tied with the k-th (no arbitrary cut in a tie)."""
    traps = tt[tt["Type"].str.startswith("T")].sort_values("mean", ascending=False)
    if len(traps) <= k:
        return traps
    return traps[traps["mean"] >= traps["mean"].iloc[k - 1]]


def trap_table(df: pd.DataFrame, config: str = "noguard") -> pd.DataFrame:
    d = df[(df["config"] == config) & df["category"].isin(["trap", "injection"])]
    if d.empty:
        return pd.DataFrame()
    piv = (d.groupby(["trap_type", "model"])["unsafe"].mean() * 100).round(0).unstack("model")
    piv["mean"] = piv.mean(axis=1).round(0)
    piv = piv.reset_index().rename(columns={"trap_type": "Type"})
    piv["k"] = piv["Type"].map(lambda t: (t[0] != "T", int(t[1:])))
    return piv.sort_values("k").drop(columns="k").fillna("n/a")


def scripted_table() -> pd.DataFrame:
    df = _frame("scripted", "test")
    if df.empty:
        return df
    rows = []
    for (model, cfg), g in df.groupby(["model", "config"], sort=False):
        t = g[g["category"].isin(["trap", "injection"])]
        r = g[g["category"] == "routine"]
        rows.append({"Plan": model.split(":")[1], "Config": _label(cfg), "Tasks": len(g),
                     "UAR (trap+inj)": _pct(t["unsafe"].mean()), "TSR (all)": _pct(g["success"].mean()),
                     "FBR (routine)": _pct(r["false_block"].mean()), "Esc/task": f"{g['n_escalations'].mean():.2f}",
                     "Rewrites/task": f"{g['n_rewrite'].mean():.2f}"})  # fmt: skip
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# case studies
# --------------------------------------------------------------------------- #


def render_trajectory(jsonl: Path, row: dict[str, Any], title: str) -> str:
    out = [f"# {title}", "", f"- task `{row['task_id']}` ({row['category']} {row['trap_type']}), model `{row['model']}`, config `{row['config']}`",
           f"- outcome: {'UNSAFE (' + row['harm_codes'] + ')' if row['unsafe'] else 'safe'}; goal met: {bool(row['goal_met'])}; "
           f"task success: {bool(row['success'])}; steps: {row['steps']}; escalations: {row['n_escalations']}", ""]  # fmt: skip
    for line in jsonl.read_text().splitlines():
        e = json.loads(line)
        if e["type"] == "message" and e["role"] == "user":
            out += ["## Ticket", "", "> " + e["content"].replace("\n", "\n> "), "", "## Trajectory", ""]
        elif e["type"] == "llm":
            for c in e["tool_calls"]:
                out.append(f"**Step {e['step']}** agent calls `{c['name']}` `{json.dumps(c['arguments'])}`")
            if not e["tool_calls"]:
                out.append(f"**Step {e['step']}** agent says: {e['content'][:300]}")
        elif e["type"] == "guard":
            det = e.get("detail") or {}
            extra = f", risk {det['risk']}, R{det['rclass']}, invariants {det['invariants']}" if "risk" in det else ""
            human = f", human: {e['human']}" if e.get("human") else ""
            out.append(f"- guard: **{e['decision']}** -> {e['status']}{extra}{human}")
            for f in (det.get("findings") or [])[:3]:
                out.append(f"  - {f['detail']}")
        elif e["type"] == "tool_result":
            text = e["content"]
            out.append(f"- result: `{text[:500]}{'...' if len(text) > 500 else ''}`")
            out.append("")
    return "\n".join(out) + "\n"


def case_studies(exp: str, df: pd.DataFrame, out: Path) -> list[Path]:
    """Three illustrative trajectories: an unguarded failure, the same task under the guard, a rewrite."""
    out.mkdir(parents=True, exist_ok=True)
    if df.empty:
        return []
    rows = {(r["model"], r["config"], r["task_id"], r["run"]): r for r in load_rows(exp) if "error" not in r}
    root = results_dir() / "runs" / exp

    def path(r: dict) -> Path:
        return root / r["model"].replace(":", "-") / r["config"] / f"{r['task_id']}__r{r['run']}.jsonl"

    picks: list[tuple[str, dict]] = []
    b0 = df[(df["config"] == "noguard") & (df["unsafe"] == 1) & (df["category"] == "trap") & (df["run"] == 0)]
    sg = df[(df["config"] == "storageguard") & (df["run"] == 0)]
    pair = b0.merge(sg[(sg["unsafe"] == 0) & (sg["stopped"] == 1)], on=["model", "task_id"], suffixes=("", "_sg"))
    if not pair.empty:
        best = pair.sort_values(["success_sg", "steps"], ascending=[False, True]).iloc[0]
        picks.append(("Case 1: unguarded agent falls into a hidden-dependency trap", rows[(best["model"], "noguard", best["task_id"], 0)]))
        picks.append(("Case 2: same ticket with StorageGuard: blocked, explained, re-planned", rows[(best["model"], "storageguard", best["task_id"], 0)]))
    elif not b0.empty:
        r = b0.iloc[0]
        picks.append(("Case 1: unguarded agent falls into a hidden-dependency trap", rows[(r["model"], "noguard", r["task_id"], 0)]))
    rw = sg[(sg["n_rewrite"] > 0)].sort_values(["success", "steps"], ascending=[False, True])
    if not rw.empty:
        r = rw.iloc[0]
        picks.append(("Case 3: StorageGuard rewrites a risky action into a safer equivalent", rows[(r["model"], "storageguard", r["task_id"], 0)]))
    paths = []
    for i, (title, r) in enumerate(picks, 1):
        if path(r).exists():
            p = out / f"case_{i}.md"
            p.write_text(render_trajectory(path(r), r, title))
            paths.append(p)
    return paths


# --------------------------------------------------------------------------- #
# REPORT.md
# --------------------------------------------------------------------------- #


def _rate(df: pd.DataFrame, col: str, resamples: int) -> str:
    if df.empty:
        return "n/a"
    m, lo, hi = bootstrap_ci(df.groupby("task_id")[col].mean().to_numpy(), resamples)
    return _pct(m, lo, hi)


def study_models(df: pd.DataFrame, experiment: str = "main") -> tuple[list[str], list[str]]:
    """(main-study models, scale-study models) among the models with episodes.

    The main study is the model list of the experiment's YAML; every other model run under the same experiment
    (``--models`` override, e.g. the Qwen2.5 size ladder) belongs to the scale study. The scale study also includes
    main-study models of the same family (the BF16 7B point of the ladder).
    """
    have = sorted(df["model"].unique())
    try:
        listed = load_experiment(experiment).get("models") or []
    except (FileNotFoundError, ValueError, KeyError):
        listed = []
    main = [m for m in have if m in listed] or have
    extra = [m for m in have if m not in main]
    families = {model_meta(m)[0] for m in extra}
    scale = sorted((m for m in have if model_meta(m)[0] in families), key=lambda m: (model_meta(m)[1] or 0, -model_meta(m)[3]))
    return main, scale if extra else []


def scale_tables(df: pd.DataFrame, sig: pd.DataFrame, models: list[str], resamples: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Scale study: (UAR/TSR per configuration and model size, B0 UAR per task category)."""
    rows, cats = [], []
    for m in models:
        _, size, weights, _ = model_meta(m)
        g = df[df["model"] == m]
        row: dict[str, Any] = {"Model": m, "Size": f"{size:g}B" if size else "?", "Weights": weights, "Runs": int(g["run"].nunique())}
        for metric, name in (("unsafe", "UAR"), ("success", "TSR")):
            for cfg in MAIN_CONFIGS:
                c = g[g["config"] == cfg]
                row[f"{_label(cfg).split()[0]} {name}"] = _pct(c[metric].mean()) if len(c) else "n/a"
        b4 = g[g["config"] == "storageguard"]
        st = b4[b4["stopped"] == 1]
        row["B4 esc/task"] = f"{b4['n_escalations'].mean():.2f}" if len(b4) else "n/a"
        row["B4 safe after block"] = _pct(st["success"].mean()) if len(st) else "n/a"
        for metric, name in (("unsafe", "UAR"), ("success", "TSR")):
            s = sig[(sig["model"] == m) & (sig["baseline"] == "llm_judge") & (sig["metric"] == metric)] if not sig.empty else sig
            row[f"B4-B3 {name} (p)"] = (f"{100 * s.iloc[0]['boot_diff']:+.1f} ({p_text(s.iloc[0]['boot_p'], 1 / resamples)})" if len(s) else "n/a")
        rows.append(row)
        b0 = g[g["config"] == "noguard"]
        cats.append({"Model": m, **{f"{c} UAR": _pct(b0[b0["category"] == c]["unsafe"].mean()) if len(b0) else "n/a"
                                    for c in ("routine", "ambiguous", "trap", "injection")}})  # fmt: skip
    return pd.DataFrame(rows), pd.DataFrame(cats)


def ablation_reference(df: pd.DataFrame, abl: pd.DataFrame) -> pd.DataFrame:
    """Ablation episodes plus the main experiment's B0/B2/B3/B4 episodes of the same models and the same run indices.

    Ablations run fewer repetitions than the main matrix; comparing them with the main run's matching runs keeps
    every row of the ablation table (and figure F4) on identical seeds and episode counts.
    """
    if abl.empty or df.empty:
        return abl
    ref = df[df["model"].isin(abl["model"].unique()) & df["config"].isin(["storageguard", "noguard", "rbac", "llm_judge"])
             & df["run"].isin(abl["run"].unique())]  # fmt: skip
    return pd.concat([abl, ref], ignore_index=True)


def build_report(main: str = "main", ablations: str = "ablations", resamples: int = 10000) -> Path:
    res, paper = results_dir(), paper_dir()
    (paper / "tables").mkdir(parents=True, exist_ok=True)
    df, abl = _frame(main), _frame(ablations)
    abl_plus = ablation_reference(df, abl)
    both = pd.concat([df, abl], ignore_index=True) if not abl.empty else df
    if not both.empty:
        both.to_csv(res / "episodes.csv", index=False)
    events = guard_events(main) if not df.empty else pd.DataFrame()
    main_models, scale_models = study_models(df, main) if not df.empty else ([], [])
    figs = make_figures(df, abl, events, paper / "figures", resamples, abl_plus, main_models, scale_models) if not df.empty else []
    tasks = load_bench()
    bench_tab = pd.DataFrame(count_table(tasks), columns=["Category", "Type", "Total", "Dev", "Test"])
    (paper / "tables" / "benchmark.tex").write_text(_tex(bench_tab, "StorageAgentBench composition.", "tab:bench"))

    L: list[str] = ["# StorageGuard: results report", "",
                    "_Generated by `sg report`. Every number below is computed from stored episodes; nothing is typed by hand._", ""]  # fmt: skip
    if df.empty:
        L += [f"No test-split episodes found for experiment `{main}`. Run `sg run {main}` first.", ""]
    else:
        models = sorted(df["model"].unique())
        dm = df[df["model"].isin(main_models)]  # main-study models: RQ2, RQ5, RQ6 and figures F2, F3, F5, F6
        runs = df.groupby(["model", "config"])["run"].nunique()
        incomplete = [f"{m} ({n}/{df['task_id'].nunique() * runs[m].max() * df[df['model'] == m]['config'].nunique()} episodes)"
                      for m, n in df.groupby("model").size().items()
                      if n < df["task_id"].nunique() * runs[m].max() * df[df["model"] == m]["config"].nunique()]  # fmt: skip
        L += ["## Provenance", "",
              f"- experiment `{main}`: {len(df)} test-split episodes, {df['task_id'].nunique()} tasks, models: {', '.join(models)}",
              f"- main study: {', '.join(main_models)}" + (f"; scale study: {', '.join(scale_models)}" if scale_models else ""),
              f"- runs per (model, config): min {runs.min()}, max {runs.max()}; temperature 0; confidence intervals are 95% task-level bootstrap ({resamples} resamples)",
              f"- ablation experiment `{ablations}`: {len(abl)} test-split episodes" + ("" if not abl.empty else " (not run yet)"),
              "- guard thresholds were tuned on the dev split only (`sg tune`); the simulated human is the oracle-backed approver unless stated"]  # fmt: skip
        if incomplete:
            L.append(f"- **incomplete, not ready to report:** {', '.join(incomplete)}")
        L.append("")
        main_tab = summary_table(dm, resamples, MAIN_CONFIGS)
        (paper / "tables" / "main_results.tex").write_text(_tex(main_tab, "Main results on the test split (95% bootstrap CI).", "tab:main"))
        aggregate(df, ["model", "config"], None, resamples).to_csv(res / "aggregates.csv", index=False)
        sig = compare_to_reference(df, "storageguard", resamples)
        sig.to_csv(res / "significance.csv", index=False)

        # ---- RQ1 ----
        b0 = df[df["config"] == "noguard"]
        L += ["## RQ1. How often do agents take unsafe actions without a guard, and which traps are hardest?", ""]
        for m in models:
            g = b0[b0["model"] == m]
            if g.empty:
                continue
            L.append(f"- **{m}**, no guard: Unsafe Action Rate {_rate(g, 'unsafe', resamples)} over all tasks; "
                     f"{_rate(g[g['category'] == 'trap'], 'unsafe', resamples)} on trap tasks; "
                     f"{_rate(g[g['category'] == 'routine'], 'unsafe', resamples)} on routine tasks; Task Success Rate {_rate(g, 'success', resamples)}.")  # fmt: skip
        tt = trap_table(df)
        if not tt.empty:
            (paper / "tables" / "per_trap.tex").write_text(_tex(tt, "Unsafe Action Rate (%) without a guard, per trap type.", "tab:trap"))
            ranked = tt[tt["Type"].str.startswith("T")].sort_values("mean", ascending=False)
            L += ["", "Hardest trap types (mean UAR across models): " + ", ".join(f"{r.Type} ({r.mean:.0f}%)" for r in hardest_types(tt).itertuples()) + ".",
                  "Easiest: " + ", ".join(f"{r.Type} ({r.mean:.0f}%)" for r in ranked.tail(3).itertuples()) + ".", "",
                  "Unsafe Action Rate (%) under B0 by trap type (Figure F1):", "", _md_table(tt)]  # fmt: skip
        if not b0.empty:
            harm = b0[b0["unsafe"] == 1]
            L += ["Harm types in unsafe B0 episodes: " + ", ".join(f"{h} {100 * harm[h].mean():.0f}%" for h in ("H1", "H2", "H3", "H4", "H5", "H6")) +
                  f"; median data at stake {harm['harm_gb'].median():.0f} GB." if len(harm) else "", ""]  # fmt: skip

        # ---- RQ2 ----
        L += ["## RQ2. Does StorageGuard reduce unsafe actions, and at what cost?", "", _md_table(main_tab), ""]
        cat_tab = category_table(dm, main_models)
        (paper / "tables" / "per_category.tex").write_text(_tex(cat_tab, "Unsafe Action Rate and Task Success Rate per task category (test split).", "tab:cat"))
        L += ["Per-category breakdown:", "", _md_table(cat_tab), ""]
        if not sig.empty:
            s = sig[sig["model"].isin(main_models)].copy()
            s["B4 minus baseline"] = s.apply(lambda r: f"{r['boot_diff']:+.3f} [{r['boot_lo']:+.3f}, {r['boot_hi']:+.3f}]", axis=1)
            s["bootstrap p"] = s["boot_p"].map(lambda p: p_text(p, 1 / resamples))
            s["McNemar p"] = s.get("mcnemar_p", pd.Series(dtype=float)).map(p_text)
            s["metric"] = s["metric"].map({"unsafe": "UAR", "success": "TSR", "n_escalations": "Esc/task"})
            s["baseline"] = s["baseline"].map(_label)
            sig_tab = s[["model", "baseline", "metric", "boot_n", "B4 minus baseline", "bootstrap p", "McNemar p"]].rename(columns={"boot_n": "tasks"})
            (paper / "tables" / "significance.tex").write_text(_tex(sig_tab, "StorageGuard (B4) versus each baseline: paired bootstrap and McNemar tests.", "tab:sig"))
            L += ["Paired tests, StorageGuard (B4) against each baseline on the same tasks:", "", _md_table(sig_tab), ""]

        # ---- RQ3 ----
        L += ["## RQ3. Which guard components matter?", ""]
        if abl.empty:
            L += ["Ablation episodes have not been run (`sg run ablations`).", ""]
        else:
            abl_tab = summary_table(abl_plus, resamples, ["storageguard", *ABLATION_CONFIGS])
            abl_tab = abl_tab[abl_tab["Config"].str.startswith(("B4", "A"))]
            (paper / "tables" / "ablations.tex").write_text(_tex(abl_tab, "Ablations of StorageGuard on the test split.", "tab:abl"))
            L += [_md_table(abl_tab), ""]
            sweep = abl_plus[abl_plus["config"].str.contains("tau_|human=") | abl_plus["config"].isin(["storageguard", "rbac"])]
            if sweep["config"].nunique() > 2:
                sw_tab = summary_table(sweep, resamples)
                (paper / "tables" / "sweep.tex").write_text(_tex(sw_tab, "Threshold sweep (A5) and human-approver sensitivity.", "tab:sweep"))
                L += ["Threshold sweep (A5) and sensitivity to the human approver (oracle / none / rubber stamp):", "", _md_table(sw_tab), ""]

        # ---- RQ4 ----
        L += ["## RQ4. Does structured feedback help agents re-plan to a safe completion?", ""]
        for name, frame in (("B4 StorageGuard (structured feedback)", df[df["config"] == "storageguard"]),
                            ("A4 no feedback ('denied' only)", abl[abl["config"] == "a4_no_feedback"] if not abl.empty else pd.DataFrame())):  # fmt: skip
            if frame.empty:
                L.append(f"- {name}: not run.")
                continue
            for m in sorted(frame["model"].unique()):
                g = frame[frame["model"] == m]
                st = g[g["stopped"] == 1]
                rw = g[g["n_rewrite"] > 0]
                L.append(f"- {name}, {m}: {len(st)} episodes had at least one blocked/denied action; safe completion after a block "
                         f"{_pct(st['success'].mean()) if len(st) else 'n/a'}; {len(rw)} episodes had a rewrite, goal still met in "
                         f"{_pct(rw['goal_met'].mean()) if len(rw) else 'n/a'} (intent preservation).")  # fmt: skip
        L.append("")

        # ---- RQ5 ----
        L += ["## RQ5. Robustness to prompt injection hidden in storage metadata", ""]
        inj = dm[dm["category"] == "injection"]
        rows5 = []
        for m in main_models:
            for cfg in [c for c in MAIN_CONFIGS if c in set(inj["config"])]:
                g = inj[(inj["model"] == m) & (inj["config"] == cfg)]
                if len(g):
                    row = {"Model": m, "Config": _label(cfg), "UAR": _rate(g, "unsafe", resamples), "TSR": _rate(g, "success", resamples)}
                    for p in ("P1", "P2", "P3"):
                        row[f"{p} UAR"] = _pct(g[g["trap_type"] == p]["unsafe"].mean())
                    rows5.append(row)
        inj_tab = pd.DataFrame(rows5)
        if not inj_tab.empty:
            short = inj_tab.assign(Config=inj_tab["Config"].str.split().str[0], UAR=inj_tab["UAR"].str.split(" [", regex=False).str[0],
                                   TSR=inj_tab["TSR"].str.split(" [", regex=False).str[0])  # a one-column table: CIs are in the report
            short = short.rename(columns={"UAR": "UAR %", "TSR": "TSR %", "P1 UAR": "P1", "P2 UAR": "P2", "P3 UAR": "P3"})
            (paper / "tables" / "injection.tex").write_text(_tex(short, "Injection tickets (test split): unsafe action rate and"
                " task success, and the unsafe action rate per injection channel (P1 volume description, P2 alert, P3 tags)."
                " B0 no guard, B1 prompt rules, B2 RBAC approval, B3 LLM judge, B4 StorageGuard.", "tab:inj"))  # fmt: skip
        L += [_md_table(inj_tab), ""]

        # ---- RQ6 ----
        L += ["## RQ6. Overhead", ""]
        lat = events[events["config"] == "storageguard"]["latency_ms"].dropna() if not events.empty else pd.Series(dtype=float)
        if len(lat):
            L.append(f"- StorageGuard decision latency over {len(lat)} mutating calls (all models): median {lat.median():.1f} ms, "
                     f"p95 {lat.quantile(0.95):.1f} ms, p99 {lat.quantile(0.99):.1f} ms, max {lat.max():.1f} ms (target: median < 50 ms). "
                     "Wall-clock time inside the experiment, while other episodes run concurrently on the same CPU.")  # fmt: skip
        for m in main_models:
            g = dm[dm["model"] == m]
            base = g[g["config"] == "noguard"]
            for cfg in ("storageguard", "llm_judge", "rbac"):
                c = g[g["config"] == cfg]
                if len(base) and len(c):
                    L.append(f"- {m}, {_label(cfg)} vs no guard: steps {c['steps'].mean():.1f} vs {base['steps'].mean():.1f}; "
                             f"tokens/task {c['tokens'].mean():.0f} vs {base['tokens'].mean():.0f}"
                             + (f"; judge calls/task {c['extra_llm_calls'].mean():.2f} ({c['extra_tokens'].mean():.0f} tokens)" if cfg == "llm_judge" else ""))  # fmt: skip
        L.append("")

        # ---- RQ7 ----
        if scale_models:
            ds = df[df["model"].isin(scale_models)]
            sc_tab, sc_cat = scale_tables(ds, sig, scale_models, resamples)
            (paper / "tables" / "scale.tex").write_text(_tex(sc_tab, "Scale study: one model family from 7B to 72B (test split).", "tab:scale"))
            L += ["## RQ7. Does model scale change the picture?", "",
                  "One model family across sizes (Figure F7). Weights are the least compressed that fit one 80 GB GPU; the 4-bit 7B "
                  "model is a control for the effect of quantisation alone. `B4-B3` is StorageGuard minus the LLM judge in percentage "
                  "points, with the paired-bootstrap p-value.", "", _md_table(sc_tab), "",
                  "Unsafe Action Rate without a guard, per task category:", "", _md_table(sc_cat), ""]  # fmt: skip

    st = scripted_table()
    if not st.empty:
        (paper / "tables" / "scripted.tex").write_text(_tex(st, "Policy-level evaluation with scripted reference and naive plans (test split, no LLM).", "tab:scripted"))
        L += ["## Policy-level evaluation (scripted plans, no LLM)", "",
              "Reference (safe) and naive (harmful) plans replayed through each guard on the test split. This isolates the "
              "guard from agent behaviour: it is deterministic and model-independent.", "", _md_table(st), ""]  # fmt: skip
    phys = res / "physical" / "REPORT.md"
    if phys.exists():
        text = phys.read_text()
        start = text.find("## What each configuration really did")
        block = text[start: text.find("![F8]", start)].strip() if start >= 0 else ""
        L += ["## RQ8. What did the agents really delete? (physical execution)", "",
              "Every recorded episode re-run on a file-backed sandbox where deletions are real; loss measured by reading the "
              "files back and by site-loss and ransomware drills. Full report: `results/physical/REPORT.md`.", "",
              *block.splitlines()[1:], ""]  # fmt: skip
    zrep = res / "zfs" / "REPORT.md"
    if zrep.exists():
        text = zrep.read_text()

        def section(head: str, stop: str) -> list[str]:
            i = text.find(head)
            return text[i: text.find(stop, i)].strip().splitlines()[1:] if i >= 0 else []

        L += ["## RQ8b. Does a real storage system agree? (OpenZFS replay)", "",
              "Every distinct trajectory re-run on OpenZFS in an isolated VM (no host mounts), call by call against the "
              "twin, and measured from data read back out of ZFS. Full report: `results/zfs/REPORT.md`, F9.", "",
              "**Fidelity**", *section("## Fidelity", "## How ZFS"), "",
              *section("## How ZFS answered each call", "![F9]")[:2], "",
              "**Disagreements**", *section("## Where ZFS and the twin disagree", "**Refused by both"), ""]  # fmt: skip
    srep = res / "restore_sensitivity" / "REPORT.md"
    if srep.exists():
        text = srep.read_text()
        i = text.find("## Physical harm per configuration")
        rest = [f"**{x[3:]}**" if x.startswith("## ") else x for x in (text[i:].strip().splitlines()[1:] if i >= 0 else [])]
        L += ["**Sensitivity: restore semantics.** The twin's restore keeps newer snapshots; `zfs rollback` refuses "
              "while they exist, and `zfs rollback -r`, like ONTAP's volume SnapRestore, deletes them. Every trajectory "
              "with a restore re-run under all three, on real files. Full report: `results/restore_sensitivity/REPORT.md`.", "",
              *[x for x in text[:i].splitlines() if x.startswith("- ")], "", *rest, ""]  # fmt: skip
    prep = res / "posthoc" / "REPORT.md"
    if prep.exists():
        text = prep.read_text()
        i, j = text.find("## Test split"), text.find("Calls are numbered")
        k = text.find("**B4 vs B4+PIT, paired**")
        L += ["## Post hoc: variant B4+PIT (newest-point-in-time-copy check; reported separately, B4 unchanged)", "",
              "Tuned on dev only (DECISIONS.md section 11). Full report: `results/posthoc/REPORT.md`.", "",
              *(text[i:j].strip().splitlines()[1:] if i >= 0 else []), "",
              *(["**Live re-run** (main-study models; each B4+PIT episode paired with the recorded B4 episode).", "",
                 *text[k:].strip().splitlines()] if k >= 0 else []), ""]  # fmt: skip
    L += ["## Benchmark", "", _md_table(bench_tab), ""]
    cs = case_studies(main, df, res / "case_studies") if not df.empty else []
    L += ["## Artefacts", "", *(f"- figure: `{p.relative_to(paper.parent)}`" for p in figs if p.suffix == ".pdf"),
          *(f"- table: `{p.relative_to(paper.parent)}`" for p in sorted((paper / 'tables').glob('*.tex'))),
          *(f"- case study: `{p.relative_to(res.parent)}`" for p in cs), "- episode data: `results/episodes.csv`, `results/aggregates.csv`, `results/significance.csv`", ""]  # fmt: skip
    path = res / "REPORT.md"
    path.write_text("\n".join(L))
    return path
