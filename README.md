# StorageGuard + StorageAgentBench

Research prototype for **"StorageGuard: Blast-Radius-Aware Guardrails for LLM Agents that Administer Enterprise Storage"**.

---
## Demo (Screen Recording)

<p align="center">
  <video src="https://github.com/user-attachments/assets/be7abd67-54da-45d6-861d-c648c2f7e510" width="80%" controls></video>
</p>

---

Everything is software-only. Nothing here talks to a real array or a cloud account; the only network calls go to the LLM endpoints you list in `configs/models.yaml`.

```
ticket -> LLM agent (tool calls) -> StorageGuard -> storage twin
                                      |  dry run on a copy of the twin
                                      |  impact analysis over a dependency graph
                                      |  risk score + hard invariants
                                      v
                        ALLOW / REWRITE / ESCALATE / BLOCK  (+ structured explanation)

after the episode:  independent harm oracle (internal state)  +  goal checker
```

| Piece | Where | What it is |
|---|---|---|
| Storage twin | `src/storageguard/twin/` | Deterministic simulated estate: pools, RAID, hot spares, volumes, snapshots, clones, replication, backups, LUN masking, zoning. 42 tools, operation log with diffs, `clone()`, replay. |
| Dependency graph | `src/storageguard/graph/` | Typed `networkx` graph built from the public API; blast-radius closure with edge-path explanations. |
| StorageGuard | `src/storageguard/guard/` | Dry run, 7 impact analysers, reversibility classes, noisy-OR risk, 7 policy-as-code invariants, safe-rewrite catalogue, structured feedback. |
| Agent harness | `src/storageguard/agent/` | OpenAI-compatible client (Ollama, vLLM, LM Studio, hosted), scripted client, tool-schema generation, response cache, JSONL trajectories. |
| StorageAgentBench | `src/storageguard/bench/` | Seeded scenario generator and 150 validated tasks: 45 routine, 25 ambiguous, 65 trap (T1-T12), 15 injection (P1-P3). |
| Evaluation | `src/storageguard/eval/` | Import-isolated harm oracle, simulated human, baselines B0-B3, ablations A1-A5, runner, bootstrap statistics, figures, LaTeX tables, `REPORT.md`. |
| MCP server | `src/storageguard/mcp_server.py` | Optional: the twin's tools behind the guard for live demos. |

Design choices and threats to validity are in [`DECISIONS.md`](DECISIONS.md). The latest results are in [`results/REPORT.md`](results/REPORT.md).

## Setup

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/) (or pip), and for live agents [Ollama](https://ollama.com).

```bash
make install            # creates ./venv and installs the package with dev tools
make test lint          # 160+ tests, ruff
make bench              # generate + validate the 150-task benchmark
make demo               # scripted agent walks into a trap; watch the guard stop it
```

> The virtualenv is `./venv` on purpose. On macOS, a `.venv` under `~/Documents` can get the *hidden* file flag, and Python then ignores the editable-install path file.

### Local models (Ollama)

```bash
ollama pull qwen2.5:7b && ollama pull llama3.1:8b && ollama pull ministral-3:8b
make ollama-models      # creates sg-* aliases with a 16k context window
make smoke              # 10 dev tasks, no guard vs StorageGuard
```

Ollama's OpenAI-compatible endpoint cannot set the context window, which is why the `sg-*` aliases exist. Keep `concurrency: 1` for Ollama; parallel requests evict each other's prompt cache.

### Optional hosted model

Uncomment an entry in `configs/models.yaml` (any OpenAI-compatible API works), export the key named in `api_key_env`, and add the model name to the experiment YAML:

```bash
export OPENAI_API_KEY=...
venv/bin/sg run main --models gpt-4o-mini
```

Keys are only ever read from the environment.

## Reproducing the paper artefacts

One command regenerates the benchmark, runs every experiment and rebuilds all figures, tables and the report:

```bash
make reproduce          # = sg reproduce
```

It is resumable (finished episodes are skipped) and cache-aware (`.cache/llm` stores every completion keyed on model, messages, tools and parameters, so a rerun makes no LLM calls and returns identical results). `make reproduce-offline` does the LLM-free part only and rebuilds the report from stored episodes.

| Step | Command | Output |
|---|---|---|
| Benchmark | `sg generate-bench && sg validate-bench` | `data/bench/tasks.json`, count table |
| Pipeline check, policy-level evaluation (no LLM) | `sg run scripted` | `results/runs/scripted/` |
| Threshold tuning on dev (no LLM) | `sg tune` | grid table; edit `configs/guard.yaml` |
| Dev run | `sg run dev` | `results/runs/dev/` |
| Main experiment (RQ1, 2, 5, 6) | `sg run main` | `results/runs/main/` |
| Ablations, threshold sweep, human-approver sensitivity (RQ3, 4) | `sg run ablations` | `results/runs/ablations/` |
| Figures F1-F6, LaTeX tables, case studies, report | `sg report` | `paper/figures/`, `paper/tables/`, `results/case_studies/`, `results/REPORT.md` |
| Physical execution: replay every recorded episode on real files (no LLM) | `sg physical main && sg physical scripted && sg physical-report` | `results/physical/`, `paper/tables/physical*.tex`, F8 |
| Paper (IEEE Access template, after the reports above; the paper sources are kept out of git) | `make paper` | `paper/build/main.pdf` |

Useful filters: `sg run main --split test --limit 20 --categories trap,injection --models qwen2.5-7b --configs "noguard;storageguard"` (configs are separated by `;` because a config may contain commas, e.g. `storageguard@tau_allow=0.1,tau_block=0.5`).

Each figure maps to one function in `eval/plots.py` and is rebuilt by `sg report`:

| Figure | Content |
|---|---|
| F1 | Unsafe Action Rate by trap type x model, no guard |
| F2 | Unsafe Action Rate and Task Success Rate per configuration |
| F3 | Unsafe Action Rate vs escalations per task across thresholds (Pareto) |
| F4 | Ablations A1-A4 |
| F5 | Injection robustness |
| F6 | Overhead: guard latency, steps and tokens |
| F7 | Scale study (Qwen2.5 7B to 72B) |
| F8 | Physical execution: data destroyed, outages and drill losses per configuration (`sg physical-report`) |

### Expected runtime and cost

| What | Episodes | Local runtime (Apple M4, 16 GB, 7-8B models) | Hosted cost |
|---|---|---|---|
| Tests, benchmark, scripted runs, tuning | 1,500 scripted | under 2 minutes | none |
| Smoke | 20 | about 15 minutes | cents |
| Main (3 models x 5 configs x 105 test tasks x 3 runs) | 4,725 | 30-60 s per episode: roughly 2-3 days sequentially; one run per config is a third of that | about 45k tokens per episode, mostly cached-prefix input: a few dollars per model on a small hosted model |
| Ablations (12 configs x 105 tasks) | 1,260 | 10-20 hours | same order |

Local inference is free but slow; the prompt (44 tool schemas, about 4.3k tokens) is evaluated once and then served from Ollama's prompt cache. On a laptop, turn **Low Power Mode off**: it throttles the GPU and slows local inference considerably. Experiments are resumable, so they can run overnight in pieces.

### On a cloud GPU (Google Colab A100)

`make colab-bundle` writes `dist/storageguard_colab.zip`, `dist/StorageGuard_Colab.ipynb` (first session) and `dist/StorageGuard_Colab_Continue.ipynb` (later sessions). Upload both to `My Drive/storageguard/`, open the notebook on an A100 runtime and run the cells in order. The notebook serves each model with vLLM, runs a smoke test before anything long, mirrors results to Drive every 5 minutes (so a disconnected session resumes), and finishes with the report and a results zip. Estimated 2-4 hours for the full plan. Step-by-step instructions and troubleshooting: [`COLAB.md`](COLAB.md).

## Demo

```bash
venv/bin/sg demo --task <id> --model qwen2.5-7b --config storageguard
venv/bin/sg demo --model scripted:naive --config noguard        # the same trap without a guard
```

prints every agent step, the guard's decision with risk, invariants and impacted entities (with dependency paths), the tool result, and the oracle's verdict.

### Real deletion

```bash
venv/bin/sg physical-demo --configs noguard,rbac,storageguard     # a T3 trap's naive plan on real files
```

runs the plan on a disposable, file-backed sandbox (one directory per site, array, pool and volume; snapshots and stored backups inside their volume; an offsite vault), lists every file the call really removed and the data it held, and keeps the post-episode files for inspection. `sg physical main` replays every recorded episode this way (the replay is checked to be exact) and measures what was really lost: data destroyed or rolled back, failed application writes, and what a site-loss and two ransomware drills can still recover. Method and results: `results/physical/REPORT.md`, DECISIONS.md section 9. The sandbox refuses any path outside its own marked directory; nothing touches a real storage system.

### On a real storage system: OpenZFS in an isolated VM (optional)

`sg zfs` runs every distinct trajectory of the study again on **OpenZFS** and compares it call by call with the twin
(results: `results/zfs/REPORT.md`, F9). It needs [Lima](https://lima-vm.io) and a disposable VM that cannot see the host:

```bash
brew install lima
limactl start --name sg-zfs --plain --cpus 4 --memory 4 --disk 30 --tty=false template:ubuntu-24.04
limactl shell sg-zfs -- bash -lc 'sudo apt-get update && sudo apt-get install -y zfsutils-linux && sudo modprobe zfs'
limactl shell sg-zfs -- sudo systemctl mask --now udisks2 zfs-zed   # optional: 5x faster dataset churn
make zfs            # = sg zfs --workers 4 && sg zfs-report  (about 35 min)
```

Isolation is checked, not assumed: the host refuses a VM that is not in plain mode or has any mount or port forward;
inside, the agent refuses to start if a host or network file system (virtiofs, 9p, sshfs, NFS, CIFS, FUSE) is
visible; pools live only on sparse files in the VM's own disk, are tagged with their session, and every dataset and
path argument is checked against that session (`tests/test_zfs.py`). `limactl delete sg-zfs` removes everything.

The replay found that a restore on ZFS (and, per NetApp's knowledge base, ONTAP's volume SnapRestore) deletes the
snapshots newer than the one restored, while the twin keeps them. `make restore-sensitivity` (no VM needed) re-measures
every restore on real files under both semantics and re-runs StorageGuard's decision with the stricter one
(`results/restore_sensitivity/REPORT.md`, DECISIONS.md X14).

### MCP server (optional)

```bash
uv pip install --python venv/bin/python -e ".[mcp]"
venv/bin/python -m storageguard.mcp_server       # stdio transport
```

Add it to an MCP client as a stdio server; every mutating tool call is checked by StorageGuard. `SG_MCP_SEED` selects the generated estate, `SG_MCP_CONFIG` the middleware (`noguard`, `rbac`, `storageguard`, ...).

## Configurations

| Name | Meaning |
|---|---|
| `noguard` (B0) | every call goes straight to the twin |
| `prompt_only` (B1) | no guard; 13 explicit safety rules in the system prompt |
| `rbac` (B2) | every destructive operation needs human approval |
| `llm_judge` (B3) | a second LLM call judges each mutating action from one-hop context |
| `storageguard` (B4) | the full guard |
| `a1_one_hop`, `a2_no_reversibility`, `a3_no_rewrites`, `a4_no_feedback` | ablations |
| `p1_pit_check` (B4+PIT) | **post hoc**: B4 plus the newest-point-in-time-copy check, found by the physical replay, tuned on dev only and reported separately (`sg posthoc`, `results/posthoc/REPORT.md`, DECISIONS.md section 11) |
| `storageguard@tau_allow=X,tau_block=Y` | threshold sweep (A5) |
| `<config>@human=none` / `@human=rubber_stamp` | no approver available / approver approves everything |

## Benchmark at a glance

| Trap | Hidden dependency |
|---|---|
| T1 | an "idle" volume is the DR replica target of a tier-1 application |
| T2 | after ransomware, the "old" snapshots are the only clean copies |
| T3 | backups live on the volume being deleted |
| T4 | the LUN being unmapped is still in use by other hosts of the cluster |
| T5 | a snapshot has a dependent clone that QA is using |
| T6 | the hot spare is taken from a rebuilding pool |
| T7 | an in-place restore discards newer data unless a pre-restore snapshot exists |
| T8 | restoring one member splits a consistency group |
| T9 | zoning a host activates a stale mapping to another tenant's volume |
| T10 | breaking (instead of pausing) replication of tier-1 data |
| T11 | the requested pool would be exhausted or over-committed |
| T12 | deleting requires unlocking immutable snapshots |
| P1-P3 | injected instructions in a volume description, an alert, or misleading tags |

Where each trap comes from (vendor documentation, public incidents such as Atlassian 2022, Azure 2023, UniSuper 2024 and GitLab 2017), where the twin is simpler or stricter than a real array, and how the benchmark relates to ToolEmu, AgentDojo, ITBench and others: [docs/GROUNDING.md](docs/GROUNDING.md). Citations: `paper/references.bib`.

`sg validate-bench` rejects any task whose reference plan does not meet the goal with zero harms, or whose naive plan does not cause harm (trap and injection tasks), or whose naive plan differs from the reference (routine tasks). Split: 30% dev / 70% test, stratified; T11 and T12 are held out of dev.

## Syllabus mapping (Information Storage Management)

| Unit | Topic | Where it appears |
|---|---|---|
| II | RAID levels, hot spares, rebuild | `Pool.raid_level`, `Disk`, `add_hot_spare` / `remove_hot_spare`, `ScenarioBuilder.fail_disk`; invariant I4, harm H6, trap T6 |
| III | FC SAN, zoning, LUN masking | `Zone`, `Mapping`, `create_zone`, `map_volume`; reachability = mapping AND zoning; invariant I3, harm H4, traps T4 and T9 |
| IV | Replication, backup, snapshots, clones, consistency groups, RPO | `ReplicationLink`, `BackupCopy`, `Snapshot`, `ConsistencyGroup`; analysers `copies_lost` and `protection_lost`; invariants I1, I2, I6; traps T1, T2, T3, T5, T7, T8, T10 |
| V | Managing and securing storage: capacity, thin provisioning, immutability, ransomware recovery, change control | capacity report and overcommit, immutable snapshots, scan status, the guard itself, RBAC baseline, prompt-injection tasks; invariants I5, I7; traps T11, T12, P1-P3 |

Every tool's docstring carries a one-line `Concept:` note explaining the storage idea behind it; those lines are also what the agent sees as tool descriptions.

## Layout

```
configs/      models.yaml  guard.yaml  policies.yaml  experiments/*.yaml
src/storageguard/
  twin/       models.py public.py operations.py state.py oplog.py serialization.py
  graph/      builder.py closure.py
  guard/      config.py analyzers.py reversibility.py risk.py invariants.py rewrites.py policy.py explain.py middleware.py
  agent/      llm_client.py tools.py loop.py cache.py prompts/
  bench/      scenario.py handbuilt.py traps.py templates.py goals.py schema.py validate.py splits.py
  eval/       harm_oracle.py human_sim.py baselines/ runner.py metrics.py stats.py tuning.py plots.py report.py
              physical_eval.py physical_report.py
  physical/   sandbox.py image.py layout.py world.py executor.py measure.py session.py   (real files, real deletion)
  mcp_server.py  cli.py  paths.py
tests/          pytest suite
data/bench/     tasks.json: the 150 StorageAgentBench tasks
results/        REPORT.md, aggregates.csv, significance.csv, episodes.csv, case_studies/, environment/,
                physical/, zfs/, restore_sensitivity/, posthoc/   (summaries; raw episode logs are not in git)
paper/figures/  F1-F9, written by `sg report`, `sg physical-report` and `sg zfs-report`
paper/references.bib  every source cited in the benchmark's grounding (docs/GROUNDING.md)
demo/           the presentation: server.py, engine.py, export.py, web/ (React; recorded data in web/public/data/)
colab/          Colab notebooks, sg_colab.py, the pinned vLLM chat template
scripts/        Colab bundle builder, Ollama model aliases, run queues
docs/           GROUNDING.md: where each trap, harm and protection comes from
```

### Not in git

- **Raw episode logs**: `results/runs/`, `results/physical/runs/` and `results/zfs/runs/` (about 300 MB in 50,000
  files). `make reproduce` regenerates them, or unzip a results archive from the Colab runs inside the project
  folder. `sg report`, `make reproduce-offline` and the demo's replays of recorded AI runs need them; every summary
  under `results/` is committed.
- **The LLM response cache** (`.cache/llm`), so a fresh clone calls the models again.
- **The paper sources** (`paper/`, apart from the generated figures and `references.bib`).
- **Environments and build output**: `venv/`, `dist/`, `demo/web/node_modules/`, `demo/web/dist/`, and result
  archives (`*.zip`).
