"""Report and figure helpers: p-value text, tie-aware trap ranking, per-category table, run-matched ablation reference."""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest

from storageguard.eval import plots
from storageguard.eval.report import ablation_reference, category_table, hardest_types, p_text


def _episodes(models, configs, runs, n_tasks=6, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    cats = ["routine", "ambiguous", "trap", "injection"]
    rows = []
    for m, c, r, t in itertools.product(models, configs, runs, range(n_tasks)):
        rows.append({"model": m, "config": c, "run": r, "task_id": f"task-{t}", "category": cats[t % 4], "trap_type": f"T{t % 3 + 1}",
                     "unsafe": int(rng.random() < 0.3), "success": int(rng.random() < 0.6), "goal_met": 1, "n_escalations": int(rng.integers(0, 3)),
                     "steps": 6, "tokens": 40000, "false_block": 0, "n_stops": 0, "n_justified_stops": 0})  # fmt: skip
    return pd.DataFrame(rows)


def test_p_text_reports_the_resolution_floor_instead_of_zero():
    assert p_text(0.0) == "<0.0001"
    assert p_text(3e-9) == "<0.0001"
    assert p_text(1 / 2000, 1 / 2000) == "<0.0005"  # a 2000-resample bootstrap cannot resolve smaller p
    assert p_text(0.0574, 1 / 10000) == "0.0574"
    assert p_text(float("nan")) == ""


def test_hardest_types_keeps_every_type_tied_with_the_cut():
    tt = pd.DataFrame({"Type": ["T1", "T2", "T3", "T4", "T5", "T6", "P1"], "mean": [100, 50, 100, 100, 100, 100, 100]})
    assert sorted(hardest_types(tt, k=4)["Type"]) == ["T1", "T3", "T4", "T5", "T6"]  # five tied at 100: none dropped, P1 excluded
    tt2 = pd.DataFrame({"Type": ["T1", "T2", "T3"], "mean": [90, 80, 70]})
    assert list(hardest_types(tt2, k=4)["Type"]) == ["T1", "T2", "T3"]


def test_category_table_covers_every_model():
    df = _episodes(["m-b", "m-a"], ["noguard", "storageguard"], [0])
    tab = category_table(df)
    assert list(tab["Model"].unique()) == ["m-a", "m-b"]
    assert len(tab) == 4 and "trap TSR" in tab.columns


def test_ablation_reference_uses_only_matching_runs_and_models():
    main = _episodes(["m-a", "m-b"], ["noguard", "rbac", "storageguard"], [0, 1, 2])
    abl = _episodes(["m-a"], ["a1_one_hop", "storageguard@tau_allow=0.1,tau_block=0.55"], [0])
    ref = ablation_reference(main, abl)
    base = ref[~ref["config"].isin(abl["config"].unique())]
    assert set(base["model"]) == {"m-a"} and set(base["run"]) == {0}
    assert len(ref) == len(abl) + 3 * 6
    assert ablation_reference(main, abl.iloc[0:0]).empty


@pytest.mark.parametrize("n_models", [1, 2, 5])
def test_pareto_draws_one_panel_per_model(tmp_path, monkeypatch, n_models):
    models = [f"model-{i}" for i in range(n_models)]
    main = _episodes(models, ["noguard", "rbac", "llm_judge", "storageguard"], [0, 1, 2])
    abl = _episodes(models[:1], ["storageguard@tau_allow=0.05,tau_block=0.4", "storageguard@tau_allow=0.5,tau_block=0.95"], [0])
    seen = {}
    monkeypatch.setattr(plots, "_save", lambda fig, out, name: seen.update(axes=[a for a in fig.axes if a.get_visible()]) or [])
    plots._style()
    plots.fig3_pareto(main, tmp_path, ablation_reference(main, abl))
    titles = [a.get_title(loc="left") for a in seen["axes"]]
    assert len(titles) == n_models
    assert titles[0].startswith("model-0 (run 0, threshold sweep)") and all(t == m for t, m in zip(titles[1:], models[1:], strict=True))


def test_ablation_figure_compares_on_the_same_runs(tmp_path, monkeypatch):
    main = _episodes(["m"], ["noguard", "storageguard"], [0, 1, 2], seed=1)
    abl = _episodes(["m"], ["a1_one_hop", "a3_no_rewrites"], [0], seed=2)
    got = {}
    real_aggregate = plots.aggregate

    def fake_aggregate(d, by, metrics, resamples):
        got["episodes"] = d.groupby("config").size().to_dict()
        return real_aggregate(d, by, metrics, 50)

    monkeypatch.setattr(plots, "aggregate", fake_aggregate)
    monkeypatch.setattr(plots, "_save", lambda fig, out, name: [])
    plots._style()
    plots.fig4_ablations(ablation_reference(main, abl), tmp_path, 50)
    assert got["episodes"] == {"a1_one_hop": 6, "a3_no_rewrites": 6, "storageguard": 6}  # B4 run 0 only, like the ablations


def test_model_meta_parses_results_names():
    assert plots.model_meta("qwen2.5-32b-int8") == ("qwen2.5", 32.0, "8-bit (GPTQ)", 8)
    assert plots.model_meta("qwen2.5-72b-awq") == ("qwen2.5", 72.0, "4-bit (AWQ)", 4)
    assert plots.model_meta("qwen2.5-7b") == ("qwen2.5", 7.0, "BF16", 16)
    assert plots.model_meta("ministral-3-8b")[:2] == ("ministral-3", 8.0)
    assert plots.model_meta("llama3.1-8b")[:2] == ("llama3.1", 8.0)
    assert plots.model_meta("qwen2.5-7b-q4")[2] == "4-bit (GGUF)"
    assert plots.model_meta("scripted:naive")[1] is None


def test_study_models_split_main_from_scale(monkeypatch):
    from storageguard.eval import report

    monkeypatch.setattr(report, "load_experiment", lambda name: {"models": ["qwen2.5-7b", "llama3.1-8b", "ministral-3-8b"]})
    names = ["qwen2.5-7b", "llama3.1-8b", "ministral-3-8b", "qwen2.5-72b-awq", "qwen2.5-14b", "qwen2.5-7b-awq"]
    main, scale = report.study_models(_episodes(names, ["noguard"], [0], n_tasks=2))
    assert main == ["llama3.1-8b", "ministral-3-8b", "qwen2.5-7b"]
    assert scale == ["qwen2.5-7b", "qwen2.5-7b-awq", "qwen2.5-14b", "qwen2.5-72b-awq"]  # by size, BF16 before 4-bit
    assert report.study_models(_episodes(names[:3], ["noguard"], [0], n_tasks=2))[1] == []  # no extra models: no scale study


def test_scale_figure_connects_the_ladder_and_marks_the_control(tmp_path, monkeypatch):
    models = ["qwen2.5-7b", "qwen2.5-7b-awq", "qwen2.5-14b", "qwen2.5-32b-int8", "qwen2.5-72b-awq"]
    df = _episodes(models, ["noguard", "llm_judge", "storageguard"], [0])
    seen = {}
    monkeypatch.setattr(plots, "_save", lambda fig, out, name: seen.update(fig=fig) or [])
    plots._style()
    plots.fig7_scale(df, tmp_path, 50, models)
    ax = seen["fig"].axes[0]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["7B\nBF16", "14B\nBF16", "32B\n8-bit", "72B\n4-bit"]
    assert len(ax.lines) >= 3 and all(len(line.get_xdata()) == 4 for line in ax.lines if line.get_linestyle() == "-")
    assert any("quantisation control" in t.get_text() for t in seen["fig"].legends[0].get_texts())
