"""Statistics: task-level bootstrap confidence intervals, paired bootstrap and McNemar tests.

Episodes of the same task (several runs) are not independent, so every interval
resamples *tasks* (cluster bootstrap): a task's value is its mean over runs.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import binomtest

METRICS = {
    "unsafe": "Unsafe Action Rate",
    "success": "Task Success Rate",
    "n_escalations": "Escalations / task",
    "steps": "Steps / task",
    "tokens": "Tokens / task",
}
LIST_COLUMNS = ("harms", "reviews", "questions", "stops")


def episodes_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Episode-level table (one row per episode) from runner summaries."""
    rows = [r for r in rows if "error" not in r]
    df = pd.DataFrame([{k: v for k, v in r.items() if k not in LIST_COLUMNS} for r in rows])
    if df.empty:
        return df
    df["tokens"] = df["prompt_tokens"] + df["completion_tokens"] + df["extra_tokens"]
    return df.sort_values(["model", "config", "task_id", "run"]).reset_index(drop=True)


def per_task(df: pd.DataFrame, metric: str) -> pd.Series:
    return df.groupby("task_id")[metric].mean()


def bootstrap_ci(values: np.ndarray, resamples: int = 10000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float, float]:
    """(mean, lo, hi) percentile bootstrap over independent units."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return (float("nan"),) * 3
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(resamples, len(values)))
    means = values[idx].mean(axis=1)
    return float(values.mean()), float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def aggregate(df: pd.DataFrame, by: list[str], metrics: list[str] | None = None, resamples: int = 10000) -> pd.DataFrame:
    """Mean and 95% CI of each metric per group (task-clustered)."""
    out = []
    for key, g in df.groupby(by, sort=False):
        key = key if isinstance(key, tuple) else (key,)
        row: dict[str, Any] = dict(zip(by, key, strict=True))
        row["n_tasks"], row["n_episodes"] = g["task_id"].nunique(), len(g)
        for m in metrics or list(METRICS):
            mean, lo, hi = bootstrap_ci(per_task(g, m).to_numpy(), resamples)
            row[m], row[f"{m}_lo"], row[f"{m}_hi"] = mean, lo, hi
        out.append(row)
    return pd.DataFrame(out)


def paired_bootstrap(a: pd.DataFrame, b: pd.DataFrame, metric: str, resamples: int = 10000, seed: int = 0) -> dict[str, float]:
    """Paired (same tasks) bootstrap of mean(a) - mean(b); two-sided p-value."""
    pa, pb = per_task(a, metric), per_task(b, metric)
    common = pa.index.intersection(pb.index)
    d = (pa[common] - pb[common]).to_numpy(dtype=float)
    if len(d) == 0:
        return {"n": 0, "diff": float("nan"), "lo": float("nan"), "hi": float("nan"), "p": float("nan")}
    rng = np.random.default_rng(seed)
    means = d[rng.integers(0, len(d), size=(resamples, len(d)))].mean(axis=1)
    p = 2 * min((means <= 0).mean(), (means >= 0).mean())
    return {"n": len(d), "diff": float(d.mean()), "lo": float(np.quantile(means, 0.025)),
            "hi": float(np.quantile(means, 0.975)), "p": float(min(1.0, max(p, 1 / resamples)))}  # fmt: skip


def mcnemar(a: pd.DataFrame, b: pd.DataFrame, metric: str) -> dict[str, float]:
    """Exact McNemar test on per-task binary outcomes (a task counts as 1 if most runs are 1)."""
    pa, pb = per_task(a, metric) >= 0.5, per_task(b, metric) >= 0.5
    common = pa.index.intersection(pb.index)
    only_a = int((pa[common] & ~pb[common]).sum())
    only_b = int((~pa[common] & pb[common]).sum())
    p = binomtest(only_a, only_a + only_b, 0.5).pvalue if only_a + only_b else 1.0
    return {"n": len(common), "only_a": only_a, "only_b": only_b, "p": float(p)}


def compare_to_reference(df: pd.DataFrame, reference: str = "storageguard", resamples: int = 10000) -> pd.DataFrame:
    """Reference config vs every other config, per model: paired bootstrap + McNemar for UAR and TSR."""
    out = []
    for model, g in df.groupby("model"):
        ref = g[g["config"] == reference]
        if ref.empty:
            continue
        for cfg in [c for c in g["config"].unique() if c != reference]:
            other = g[g["config"] == cfg]
            for metric in ("unsafe", "success", "n_escalations"):
                pb = paired_bootstrap(ref, other, metric, resamples)
                row = {"model": model, "reference": reference, "baseline": cfg, "metric": metric, **{f"boot_{k}": v for k, v in pb.items()}}
                if metric != "n_escalations":
                    mc = mcnemar(ref, other, metric)
                    row.update({"mcnemar_ref_only": mc["only_a"], "mcnemar_base_only": mc["only_b"], "mcnemar_p": mc["p"]})
                out.append(row)
    return pd.DataFrame(out)
