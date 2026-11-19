"""`sg` command line: generate-bench, validate-bench, run, report, demo, reproduce, tune."""

from __future__ import annotations

import asyncio
import json
from collections import Counter

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    help="StorageGuard / StorageAgentBench command line.", no_args_is_help=True, add_completion=False
)
console = Console()


@app.callback()
def _main() -> None:
    """StorageGuard / StorageAgentBench."""


def _count_table(tasks: list) -> Table:
    from .bench.splits import count_table

    table = Table(title=f"StorageAgentBench: {len(tasks)} tasks")
    for col in ("category", "trap type", "total", "dev", "test"):
        table.add_column(col, justify="right" if col in ("total", "dev", "test") else "left")
    cat_tot: Counter[str] = Counter()
    for cat, trap, n, d, t in count_table(tasks):
        table.add_row(cat, trap, str(n), str(d), str(t))
        cat_tot[cat] += n
    table.add_section()
    for cat, n in cat_tot.items():
        table.add_row(cat, "all", str(n), "", "")
    return table


@app.command("generate-bench")
def generate_bench_cmd(
    scale: float = typer.Option(1.0, help="1.0 = 150 tasks; 6.7 = ~1000 tasks."),
    seed: int = typer.Option(0, help="Base seed of the benchmark."),
    out: str = typer.Option(None, help="Output JSON (default data/bench/tasks.json)."),
) -> None:
    """Generate the benchmark (only tasks that pass validation are kept)."""
    from pathlib import Path

    from .bench.splits import generate_bench, save_bench

    tasks, rejected = generate_bench(scale, seed)
    path = save_bench(tasks, Path(out) if out else None)
    console.print(_count_table(tasks))
    if rejected:
        console.print(f"rejected while generating: {rejected}")
    console.print(f"wrote {len(tasks)} tasks to {path}")


@app.command("validate-bench")
def validate_bench_cmd(
    path: str = typer.Option(None, help="Benchmark JSON (default data/bench/tasks.json)."),
) -> None:
    """Re-validate every stored task: reference plan safe + successful, naive plan harmful on traps."""
    from pathlib import Path

    from .bench.splits import load_bench, task_twin
    from .bench.validate import validate_task

    tasks = load_bench(Path(path) if path else None)
    bad = 0
    for t in tasks:
        res = validate_task(t, task_twin(t))
        if not res.ok:
            bad += 1
            console.print(f"[red]INVALID[/red] {t.id} ({t.task_type}): {'; '.join(res.errors)}")
    console.print(_count_table(tasks))
    need = {"routine": 45, "ambiguous": 25, "trap": 65, "injection": 15}
    have = Counter(t.category for t in tasks)
    per_trap = Counter(t.trap_type for t in tasks if t.category == "trap")
    dev_holdout = [t.id for t in tasks if t.split == "dev" and t.trap_type in ("T11", "T12")]
    problems = [f"{c}: {have[c]} < {n}" for c, n in need.items() if have[c] < n]
    problems += [f"{k}: only {v} tasks" for k, v in per_trap.items() if v < 5]
    problems += ["held-out trap types leaked into dev"] if dev_holdout else []
    console.print(f"valid tasks: {len(tasks) - bad}/{len(tasks)}")
    if bad or problems:
        console.print(f"[red]FAILED[/red] {problems}")
        raise typer.Exit(1)
    console.print("[green]benchmark is valid[/green]")


@app.command()
def run(
    experiment: str = typer.Argument(..., help="Experiment name in configs/experiments/ or a YAML path."),
    split: str = typer.Option(None, help="dev | test | all (overrides the YAML)."),
    limit: int = typer.Option(None, help="Only the first N tasks."),
    categories: str = typer.Option(None, help="Comma-separated categories."),
    models: str = typer.Option(None, help="Comma-separated model names (overrides the YAML)."),
    configs: str = typer.Option(None, help="Semicolon-separated configs (overrides the YAML)."),
    runs: int = typer.Option(None, help="Runs per (model, config) (overrides the YAML)."),
    name: str = typer.Option(None, help="Store results under this experiment name instead of the YAML's."),
) -> None:
    """Run an experiment matrix (resumable; cached LLM calls are free)."""
    from .eval.runner import load_experiment, run_experiment

    def lst(x: str | None) -> list[str] | None:
        return [p for p in x.split(";" if ";" in x else ",")] if x else None

    exp = load_experiment(experiment)
    if name:
        exp["name"] = name
    if runs is not None:
        exp["runs"] = runs
        exp.pop("runs_per_config", None)
    cfgs = configs.split(";") if configs else None  # configs may contain commas -> separate with ';'
    rows = asyncio.run(run_experiment(exp, split, limit, lst(categories), lst(models), cfgs))
    ok = [r for r in rows if "error" not in r]
    table = Table(title=f"{exp['name']}: {len(ok)} episodes")
    for col in ("model", "config", "n", "UAR", "TSR", "esc/task", "steps"):
        table.add_column(col)
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in ok:
        groups.setdefault((r["model"], r["config"]), []).append(r)
    for (m, c), rs in groups.items():
        n = len(rs)
        table.add_row(m, c, str(n), f"{sum(r['unsafe'] for r in rs) / n:.2f}", f"{sum(r['success'] for r in rs) / n:.2f}",
                      f"{sum(r['n_escalations'] for r in rs) / n:.2f}", f"{sum(r['steps'] for r in rs) / n:.1f}")  # fmt: skip
    console.print(table)


@app.command("audit-args")
def audit_args_cmd(
    delete: bool = typer.Option(False, help="Delete the affected episodes so the next `sg run` redoes them."),
    mirror: str = typer.Option(
        None, help="Also delete them under this copy of results/ (e.g. the Drive state)."
    ),
) -> None:
    """Find finished episodes judged on non-canonical argument types (DECISIONS.md, C12)."""
    from pathlib import Path

    from .eval.audit import affected_episodes
    from .paths import results_dir

    res_root = results_dir()
    eps = affected_episodes(res_root / "runs")
    console.print(
        f"audit-args: {len(eps)} finished episode(s) had a guarded call with non-canonical argument types"
    )
    for key, n in Counter("/".join(p.relative_to(res_root / "runs").parts[:3]) for p in eps).most_common():
        console.print(f"  {n:5d}  {key}")
    if delete and eps:
        for p in eps:
            for q in (p, p.with_suffix(".jsonl")):
                q.unlink(missing_ok=True)
                if mirror:
                    (Path(mirror) / q.relative_to(res_root)).unlink(missing_ok=True)
        console.print("  deleted: the next `sg run` of these experiments redoes them")


@app.command()
def report(
    main: str = typer.Option("main", help="Main experiment name."),
    ablations: str = typer.Option("ablations", help="Ablation experiment name."),
    resamples: int = typer.Option(10000, help="Bootstrap resamples."),
) -> None:
    """Build the episode CSV, statistics, figures, LaTeX tables, case studies and REPORT.md."""
    from .eval.report import build_report

    path = build_report(main, ablations, resamples)
    console.print(f"wrote {path}")


@app.command()
def demo(
    task: str = typer.Option(None, help="Task id (default: a T3 trap from the dev split)."),
    model: str = typer.Option(
        "scripted:naive", help="Model name from configs/models.yaml, scripted:naive or scripted:reference."
    ),
    config: str = typer.Option(
        "storageguard", help="noguard | prompt_only | rbac | llm_judge | storageguard | a1_one_hop | ..."
    ),
) -> None:
    """Run one episode and pretty-print every agent step, guard decision and result."""
    from .agent.cache import ResponseCache
    from .agent.llm_client import load_models, make_client
    from .bench.splits import load_bench
    from .eval.runner import run_one

    tasks = load_bench()
    chosen = (
        next((t for t in tasks if t.id == task), None)
        if task
        else next(t for t in tasks if t.trap_type == "T3" and t.split == "dev")
    )
    if chosen is None:
        raise typer.BadParameter(f"unknown task id {task}")
    client = None if model.startswith("scripted:") else make_client(load_models()[model], ResponseCache())
    console.print(
        Panel(
            chosen.instruction,
            title=f"ticket {chosen.id}  [{chosen.category} {chosen.trap_type or ''}]  model={model}  config={config}",
        )
    )
    colours = {"ALLOW": "green", "REWRITE": "yellow", "ESCALATE": "magenta", "BLOCK": "red"}

    def show(kind: str, d: dict) -> None:
        if kind == "llm":
            for c in d["tool_calls"]:
                console.print(
                    f"[bold cyan]step {d['step']}[/bold cyan] agent -> [bold]{c['name']}[/bold] {json.dumps(c['arguments'])}"
                )
            if not d["tool_calls"]:
                console.print(f"[bold cyan]step {d['step']}[/bold cyan] agent said: {d['content'][:300]}")
        elif kind == "guard":
            col = colours.get(d["decision"], "white")
            det = d.get("detail") or {}
            line = f"   guard: [{col}]{d['decision']}[/{col}] -> {d['status']}"
            if "risk" in det:
                line += f"  risk={det['risk']} R{det['rclass']} invariants={det['invariants']}"
            if d.get("human"):
                line += f"  human={d['human']}"
            console.print(line + f"  ({d['latency_ms']:.1f} ms)")
            for f in det.get("findings", [])[:4]:
                console.print(f"     - {f['detail']}")
        elif kind == "tool_result":
            text = d["content"]
            try:
                obj = json.loads(text)
                for imp in obj.get("impacted", [])[:5]:
                    console.print(f"     impacted: {imp['path']}")
                for alt in obj.get("suggested_alternatives", [])[:3]:
                    console.print(f"     suggestion: {alt}")
            except (json.JSONDecodeError, AttributeError):
                pass
            console.print(f"   result: [dim]{text[:400]}{'...' if len(text) > 400 else ''}[/dim]")

    row = run_one(chosen, model, config, client=client, on_event=show)
    verdict = "[green]SAFE[/green]" if not row["unsafe"] else "[red]UNSAFE[/red]"
    console.print(Panel(
        f"harm oracle: {verdict} {row['harm_codes']}\ngoal met: {bool(row['goal_met'])}   task success: {bool(row['success'])}\n"
        f"steps: {row['steps']}  escalations: {row['n_escalations']}  blocks: {row['n_block']}  rewrites: {row['n_rewrite']}",
        title="outcome"))  # fmt: skip


@app.command("physical")
def physical_cmd(
    experiment: str = typer.Argument(
        "main", help="Experiment whose recorded episodes are replayed (main, scripted, ...)."
    ),
    models: str = typer.Option(
        None, help="Comma-separated model names (default: all with recorded episodes)."
    ),
    configs: str = typer.Option(None, help="Comma-separated configs (default: all)."),
    tasks: str = typer.Option(None, help="Comma-separated task ids (default: all)."),
    workers: int = typer.Option(8, help="Parallel worker processes."),
    sandbox_dir: str = typer.Option(
        None, help="Where the disposable sandboxes are created (default: the system temp dir)."
    ),
) -> None:
    """Re-run recorded episodes on a file-backed sandbox where deletions are real (no LLM calls, resumable)."""
    import time

    from .eval.physical_eval import plan_jobs, run_study

    jobs = plan_jobs(experiment, models.split(",") if models else None, configs.split(",") if configs else None,
                     set(tasks.split(",")) if tasks else None)  # fmt: skip
    t0 = time.time()
    stats: Counter[str] = Counter()

    def progress(i: int, n: int, res: dict) -> None:
        stats[
            "skipped"
            if res.get("skipped")
            else "error"
            if res.get("error")
            else "exact"
            if res.get("exact")
            else "diverged"
        ] += 1
        if i % 250 == 0 or i == n:
            console.print(f"  {i}/{n}  {dict(stats)}  {(time.time() - t0) / i:.2f}s/episode")

    console.print(f"[physical] {experiment}: {len(jobs)} recorded episodes")
    run_study(jobs, workers, sandbox_dir, progress)
    console.print(f"done in {(time.time() - t0) / 60:.1f} min: {dict(stats)}; now run `sg physical-report`")


@app.command("physical-report")
def physical_report_cmd(
    experiment: str = typer.Option("main", help="Replayed experiment."),
    resamples: int = typer.Option(10000, help="Bootstrap resamples."),
) -> None:
    """Tables, figure F8 and results/physical/REPORT.md from the physical replays."""
    from .eval.physical_report import build_physical_report

    console.print(f"wrote {build_physical_report(experiment, 'scripted', resamples)}")


@app.command("zfs")
def zfs_cmd(
    experiments: str = typer.Option(
        "main,scripted", help="Comma-separated experiments whose episodes are replayed."
    ),
    workers: int = typer.Option(4, help="Parallel sessions in the VM."),
    limit: int = typer.Option(None, help="Only the first N trajectories (smoke test)."),
    sandbox_dir: str = typer.Option(None, help="Where the host-side export sandboxes are created."),
) -> None:
    """Replay every executed trajectory on OpenZFS inside the isolated Lima VM `sg-zfs` (resumable).

    Needs `limactl` and the VM (see `make zfs-vm`); refuses to run unless the VM is in plain mode
    with no host mounts and no port forwards.
    """
    import time

    from .eval.zfs_eval import collect, run_zfs_study, save_index

    trajs, episodes = collect(tuple(experiments.split(",")))
    save_index(trajs, episodes)
    todo = sorted(trajs.values(), key=lambda t: (t.task_id, t.key))[: limit or None]
    console.print(f"[zfs] {len(episodes)} episodes -> {len(trajs)} trajectories; running {len(todo)}")
    t0 = time.time()
    stats: Counter[str] = Counter()

    def progress(i: int, n: int, res: dict) -> None:
        stats["skipped" if res.get("skipped") else "error" if res.get("error") else
              "identical" if res.get("identical") else "different"] += 1  # fmt: skip
        if i % 50 == 0 or i == n:
            console.print(f"  {i}/{n}  {dict(stats)}  {(time.time() - t0) / i:.2f}s/trajectory")

    run_zfs_study(todo, workers, sandbox_dir, progress)
    console.print(f"done in {(time.time() - t0) / 60:.1f} min: {dict(stats)}; now run `sg zfs-report`")


@app.command("zfs-report")
def zfs_report_cmd() -> None:
    """Fidelity tables and results/zfs/REPORT.md from the OpenZFS replays."""
    from .eval.zfs_report import build_zfs_report

    console.print(f"wrote {build_zfs_report()}")


@app.command("restore-sensitivity")
def restore_sensitivity_cmd(
    report_only: bool = typer.Option(False, "--report-only", help="Rebuild the report from saved results."),
) -> None:
    """Re-measure every restore on real files with rollback semantics (refuse; destroy newer, as -r and SnapRestore)."""
    from .eval import restore_sensitivity

    if not report_only:
        out = restore_sensitivity.run()
        console.print(
            f"{out['affected_trajectories']} trajectories with a restore re-run under {restore_sensitivity.MODES}"
        )
        restore_sensitivity.guard_shadow()
    console.print(f"wrote {restore_sensitivity.build_report()}")


@app.command("posthoc")
def posthoc_cmd(
    action: str = typer.Argument(..., help="tune (dev only) | test | report"),
    severity: float = typer.Option(None, help="P1 severity for `test` (default: the configured one)."),
) -> None:
    """Post-hoc variant P1 (newest-point-in-time-copy check): tune on dev, evaluate on test, report separately."""
    from .eval import posthoc
    from .guard.config import GuardConfig

    if action == "tune":
        out = posthoc.tune_p1()
        console.print(f"chosen severity on dev: {out['chosen']} (results/posthoc/dev_grid.json)")
    elif action == "test":
        sev = severity if severity is not None else GuardConfig.load().sev(posthoc.CODE)
        out = posthoc.evaluate_p1(sev)
        console.print(json.dumps({k: out[k] for k in ("severity", "shadow", "verdicts")}, indent=1))
    elif action == "report":
        console.print(f"wrote {posthoc.build_posthoc_report()}")
    else:
        raise typer.BadParameter("action must be tune, test or report")


@app.command("physical-demo")
def physical_demo_cmd(
    task: str = typer.Option(None, help="Task id (default: a T3 trap of the test split)."),
    plan: str = typer.Option("naive", help="naive | reference (scripted plan of the task)."),
    configs: str = typer.Option("noguard,storageguard", help="Comma-separated configs to compare."),
    sandbox_dir: str = typer.Option(None, help="Where the sandboxes are created (kept for inspection)."),
) -> None:
    """Run one task's plan on real files under several configs and show what was really deleted."""
    from .bench.splits import load_bench
    from .eval.physical_eval import PhysicalOptions
    from .eval.runner import run_one

    tasks = load_bench()
    chosen = (
        next((t for t in tasks if t.id == task), None)
        if task
        else next(t for t in tasks if t.trap_type == "T3" and t.split == "test")
    )
    if chosen is None:
        raise typer.BadParameter(f"unknown task id {task}")
    console.print(
        Panel(
            chosen.instruction,
            title=f"ticket {chosen.id}  [{chosen.category} {chosen.trap_type or ''}]  plan={plan}",
        )
    )
    opts = PhysicalOptions(base_dir=sandbox_dir, keep=True)
    for cfg in configs.split(","):
        row = run_one(chosen, f"scripted:{plan}", cfg, physical=opts)
        p = row["physical"]
        table = Table(title=f"{cfg}: oracle {'UNSAFE ' + row['harm_codes'] if row['unsafe'] else 'safe'}; "
                            f"physical {'HARM' if p['phys_unsafe'] else 'no harm'}")  # fmt: skip
        for col in ("call", "removed from disk", "bytes", "data it held (newest commit)"):
            table.add_column(col)
        for d in p["deletions"]:
            for f in d["files"]:
                held = ", ".join(f"{k}@{v}" for k, v in f["held"].items()) or "(unreadable)"
                table.add_row(f"{d['op']} ({d['action']})", f["path"], str(f["bytes"]), held)
        console.print(table)
        console.print(f"  destroyed: {[d['owner'] for d in p['destroyed']]}  rolled back: {[d['owner'] for d in p['rolled_back']]}"
                      f"  outage: {[o['volume'] for o in p['outage']]}  site drill: {[x['owner'] for x in p['site_loss']]}"
                      f"  ransomware: {[x['owner'] for x in p['ransomware'] + p['ransomware_admin']]}")  # fmt: skip
        console.print(f"  post-episode files kept at: {p['sandbox']}")


@app.command()
def tune(resamples: int = typer.Option(0, help="unused; kept for symmetry")) -> None:
    """Grid-search tau_allow / tau_block on the DEV split with scripted plans (no LLM needed)."""
    from .eval.tuning import tune_thresholds

    best, table = tune_thresholds()
    console.print(table)
    console.print(f"best on dev: tau_allow={best[0]} tau_block={best[1]} (edit configs/guard.yaml to apply)")


@app.command()
def reproduce(
    skip_llm: bool = typer.Option(
        False, help="Only regenerate the benchmark, scripted runs and the report from existing results."
    ),
) -> None:
    """Regenerate the benchmark, run every experiment (cache-aware) and rebuild all outputs."""
    from .bench.splits import generate_bench, save_bench
    from .eval.report import build_report
    from .eval.runner import load_experiment, run_experiment

    tasks, _ = generate_bench()
    save_bench(tasks)
    console.print(_count_table(tasks))
    names = ["scripted"] if skip_llm else ["scripted", "dev", "main", "ablations"]
    for name in names:
        asyncio.run(run_experiment(load_experiment(name)))
    console.print(f"wrote {build_report('main', 'ablations', 10000)}")


if __name__ == "__main__":  # pragma: no cover
    app()
