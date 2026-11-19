"""Build the Colab notebooks (kept as code so they are reviewable and testable).

- colab/StorageGuard_Colab.ipynb: the first session (install, smoke test, full run).
- colab/StorageGuard_Colab_Continue.ipynb: later sessions: the models not run yet, and Llama once Meta approves it.
"""

import json
from pathlib import Path

GPU_CHECK = """import subprocess
gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                     capture_output=True, text=True).stdout.strip()
print(gpu or "NO GPU")
assert gpu, "No GPU: Runtime -> Change runtime type -> A100 GPU"
if "A100" not in gpu and "H100" not in gpu:
    print("WARNING: not an A100/H100. 7-8B models still fit on a 24 GB GPU (L4), but expect it to be slower.")"""

# Everything after the settings block of cell 2; shared by both notebooks.
PREAMBLE = """import os, sys, time, zipfile, pathlib, subprocess
from google.colab import drive, userdata

drive.mount("/content/drive")
bundles = sorted(pathlib.Path(DRIVE_DIR).glob("storageguard_colab*.zip"), key=os.path.getmtime)
assert bundles, f"Upload storageguard_colab.zip to {DRIVE_DIR} first"
bundle = bundles[-1]                          # the newest upload, also if Drive kept an older copy
zipfile.ZipFile(bundle).extractall("/content")  # always the newest code; results are never in the zip
print("code from", bundle.name, "uploaded", time.strftime("%Y-%m-%d %H:%M", time.localtime(bundle.stat().st_mtime)))
os.chdir("/content/storageguard")
os.environ["SG_DRIVE_STATE"] = f"{DRIVE_DIR}/state"
try:
    os.environ["HF_TOKEN"] = userdata.get("HF_TOKEN")
    print("HF_TOKEN loaded")
except Exception as exc:  # secret missing or notebook access not granted
    print(f"No HF_TOKEN ({type(exc).__name__}). Qwen and Mistral will work; Llama downloads will fail.")

def run(*args):
    \"\"\"Run the driver, stream its output here, and stop the notebook if it fails.\"\"\"
    p = subprocess.Popen([sys.executable, "-u", "colab/sg_colab.py", *map(str, args)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        print(line, end="")
    if p.wait() != 0:
        raise RuntimeError(f"step failed with exit code {p.returncode}; read the messages above")

def show_report():
    from IPython.display import Markdown, Image, display
    display(Markdown(pathlib.Path("results/REPORT.md").read_text()))
    for png in sorted(pathlib.Path("paper/figures").glob("*.png")):
        print(png.name)
        display(Image(filename=str(png), width=900))

def download_results():
    run("pack")   # zips results/, paper/, data/bench and configs into DRIVE_DIR
    from google.colab import files
    latest = max(pathlib.Path(DRIVE_DIR).glob("storageguard_results_*.zip"), key=os.path.getmtime)
    print("saved to Drive:", latest)
    files.download(str(latest))

print("project ready at", os.getcwd())"""

INSTALL_MD = "## 3. Install (about 5-10 minutes)\n\nCreates two isolated environments: one for the experiment harness (Python 3.11) and one for vLLM (Python 3.12), then runs the fast unit tests."

CELLS = [
("md", """# StorageGuard experiments on a Colab GPU

This notebook runs every LLM experiment of the StorageGuard paper on one GPU:

| Stage | What | Estimated time on an A100 |
|---|---|---|
| 1-3 | check GPU, mount Drive, install | 10 min |
| 4 | **smoke test**: 12 short episodes per model (first run also downloads ~15 GB per model) | 20-30 min |
| 5 | full run: dev split, main matrix (3 models x 5 configs x 3 runs), ablations, report | 2-4 h (estimate) |
| 6-7 | show the report, download the results | 1 min |

**Before you start:** `Runtime -> Change runtime type -> A100 GPU`. For Llama (gated, approved by Meta), add a Hugging Face token as a Colab secret named `HF_TOKEN` (key icon in the left sidebar, enable *Notebook access*).

Everything is resumable: results are mirrored to Google Drive every 5 minutes. If the session disconnects, reconnect and run cells 1, 2, 3 again, then cell 5; finished episodes are skipped."""),
("md", "## 1. Check the GPU"),
("code", GPU_CHECK),
("md", "## 2. Settings, Google Drive and the project\n\nUpload `storageguard_colab.zip` to `MyDrive/storageguard/` first (see COLAB.md)."),
("code", """# ---- settings -------------------------------------------------------------
BACKEND   = "vllm"                                  # "ollama" = fallback if vLLM cannot be installed or started
MODELS    = "qwen2.5-7b,llama3.1-8b,ministral-3-8b" # scale study: see StorageGuard_Colab_Continue.ipynb, Part B
MAIN_RUNS = 3                                       # runs per (model, config) in the main experiment
DRIVE_DIR = "/content/drive/MyDrive/storageguard"   # where you uploaded storageguard_colab.zip
# ---------------------------------------------------------------------------
""" + "\n" + PREAMBLE),
("md", INSTALL_MD),
("code", """run("setup", "--backend", BACKEND)"""),
("md", """## 4. Smoke test (do not skip)

For each model: start the server, run 3 dev-split trap tasks under 4 configurations (no guard, RBAC, LLM judge, StorageGuard), and check that tool calls parse, the guard is reached, the judge is called and episodes finish. It first checks (in seconds) that your Hugging Face token can download each model. A model that fails is reported with the reason and the next one is still tested; a summary comes at the end. The first run downloads each model."""),
("code", """run("smoke", "--backend", BACKEND, "--models", MODELS)"""),
("md", """## 5. Full run

Primary model (Qwen2.5-7B): dev split (all 9 configs), main matrix, ablations + threshold sweep + approver sensitivity. Other models: main matrix. Then the report. Safe to re-run: finished episodes are skipped and results are restored from Drive. To add a model later (for example once its licence is approved), smoke-test it alone and run this cell with only that model: `run("all", "--backend", BACKEND, "--models", "llama3.1-8b", "--main-runs", MAIN_RUNS)`."""),
("code", """run("all", "--backend", BACKEND, "--models", MODELS, "--main-runs", MAIN_RUNS)"""),
("md", "## 6. Report"),
("code", "show_report()"),
("md", "## 7. Download the results"),
("code", "download_results()   # also saved to DRIVE_DIR"),
("md", """## Troubleshooting

| Symptom | Fix |
|---|---|
| Session disconnected | Reconnect, run cells 1, 2, 3, then 5 again. Nothing finished is lost. |
| `403` / "not in the authorized list" for Llama or Mistral | The account behind `HF_TOKEN` has no access yet. Open the model page while logged in: submit the licence form, or wait if it says "awaiting review" (Llama is approved by hand). If it says "granted", use a classic *Read* token (a fine-grained one needs "Read access to contents of all public gated repos"), update the secret and re-run cell 2. Meanwhile run the other models: set `MODELS` without it. |
| `401` for Llama or Mistral | No token or a rejected one: check the `HF_TOKEN` secret (Notebook access on) and re-run cell 2. |
| vLLM install fails or the server exits at start | Read the log tail printed above. Setup installs the pinned vLLM 0.33.0 with matching packages; keep it for the whole study. Last resort: `BACKEND = "ollama"` (results stored as `<model>-q4`, never mixed with vLLM ones). |
| Mistral-format model: `400 ... missing ... 'force_tool_names'` | mistral_common too new for vLLM 0.33.0: run cell 3 again with the current zip, or `!uv pip install --python /content/venv-vllm/bin/python "mistral-common[image]==1.12.0"`. |
| CUDA out of memory | Add `"--concurrency", 8` to the `run(...)` calls. |
| Smoke test: "never produced a valid tool call" | The tool-call parser or chat template does not match the model. Drop that model from `MODELS` and report it as a limitation, or try the Ollama backend. |
| Want to check progress | `!find results/runs -name '*.json' | wc -l` in a new cell, or look at `results/runs` in the Files pane. |"""),
]


CONTINUE_CELLS = [
("md", """# StorageGuard: continue the study

Use this notebook for every session after the first. The first session installed everything and Qwen2.5-7B passed its smoke test. Mistral-7B-Instruct-v0.3 was dropped: it cannot drive the tool interface (it writes command lines instead of tool calls), so Mistral's current small model, **Ministral 3 8B** (2512, Apache-2.0), takes its place. Llama 3.1 waits for Meta's approval.

| Part | Cells | What | Time on an A100 (estimate) |
|---|---|---|---|
| A | 1-3 | GPU check, Drive, install (a new runtime starts empty) | ~10 min |
| A | 4 | smoke test Ministral 3 8B (first download ~18 GB) | ~5-10 min |
| A | 5 | full run: Qwen 7B (dev, main, ablations) and Ministral 3 8B (main), then the report | ~1.5-2.5 h |
| A | 6-7 | look at the report, download the interim results | 1 min |
| B | 8-10 | **scale study** (recommended): Qwen2.5 14B, 32B and 72B, plus a 4-bit 7B control (80 GB A100) | ~1.5-2 h (1 run each) |
| C | 11-14 | **Llama 3.1, once Meta approves**: check access, smoke test, main runs, final report and download | ~45 min |

**Before you start**

1. If another notebook is still connected, stop its runtime there: **Runtime -> Disconnect and delete runtime**. Otherwise two A100s are billed at once.
2. Upload the new `storageguard_colab.zip` into `MyDrive/storageguard/` and choose **Replace existing file** if Drive asks (cell 2 uses the newest zip either way).
3. **Runtime -> Change runtime type -> A100 GPU**.

Parts B and C are independent: run them in any order, in this session (after cell 5) or in a later one (then run cells 1, 2, 3 first). Nothing finished is ever run twice: results are mirrored to `MyDrive/storageguard/state/` and restored at the start of every run."""),
("md", "## 1. Check the GPU"),
("code", GPU_CHECK),
("md", "## 2. Settings, Google Drive and the project"),
("code", """# ---- settings -------------------------------------------------------------
BACKEND   = "vllm"
MODELS    = "qwen2.5-7b,ministral-3-8b"             # Part A: the main models that need no approval
SCALE     = "qwen2.5-7b-awq,qwen2.5-14b,qwen2.5-32b-int8,qwen2.5-72b-awq"  # Part B (80 GB A100)
# on a 40 GB A100: SCALE = "qwen2.5-7b-awq,qwen2.5-14b-int8,qwen2.5-32b-awq"
LATER     = "llama3.1-8b"                           # Part C: once Meta approves the licence
MAIN_RUNS = 3                                       # runs per (model, config) for the main models
SCALE_RUNS = 1                                      # runs per config in Part B; 3 matches Part A (~3.5-4 h instead of ~1.5-2 h)
DRIVE_DIR = "/content/drive/MyDrive/storageguard"   # where storageguard_colab.zip is
# ---------------------------------------------------------------------------
""" + "\n" + PREAMBLE),
("md", INSTALL_MD),
("code", """run("setup", "--backend", BACKEND)"""),
("md", """## 4. Smoke test Ministral 3 8B

Qwen passed in the first session, so only Ministral is tested here (12 short episodes). It must end with `smoke test PASSED`. If it fails, the output shows what the model wrote instead of tool calls; continue with Qwen alone (`MODELS = "qwen2.5-7b"` in cell 2, run cell 2 again, then cell 5)."""),
("code", """run("smoke", "--backend", BACKEND, "--models", "ministral-3-8b")"""),
("md", """## 5. Full run: Qwen and Ministral

Qwen2.5-7B: dev split (9 configs), main matrix (5 configs x 3 runs), ablations, threshold sweep and approver sensitivity. Ministral 3 8B: main matrix. Then the report.

Keep the tab open. If the session disconnects: run cells 1, 2, 3, then this cell again; finished episodes are skipped. Progress lines look like `600/1560  0.9s/episode  eta 14 min`."""),
("code", """run("all", "--backend", BACKEND, "--models", MODELS, "--main-runs", MAIN_RUNS)"""),
("md", "## 6. Report (so far)"),
("code", "show_report()"),
("md", "## 7. Download the interim results\n\nA copy is also saved in `MyDrive/storageguard/`."),
("code", "download_results()"),
("md", """---
# Part B: scale study (recommended)

Does a larger model still need the guard? Same family (Qwen2.5), same tool format, four sizes on an 80 GB A100: 7B (Part A, full precision), **14B** (full precision, 29.5 GB), **32B** (official GPTQ 8-bit, 35.1 GB; near-lossless) and **72B** (official AWQ 4-bit, 41.6 GB). Larger precisions do not fit with room for the KV cache. The **7B in AWQ 4-bit** is a control: comparing it with the full-precision 7B shows how much 4-bit weights alone change the results.

Main matrix only (5 configs), `SCALE_RUNS` runs each. About 120 GB of downloads: when the disk runs short, the driver deletes the cached weights of models that are already finished (they would simply be downloaded again if needed). A model too large for the GPU is rejected in seconds with the 40 GB alternative."""),
("md", "## 8. Check the larger models can run here (seconds)\n\nGPU memory and download access for each model."),
("code", """run("access", "--models", SCALE)"""),
("md", """## 9. Smoke test and main runs, model by model

Each model is downloaded and loaded once: smoke test first, its runs only if the smoke test passes. Roughly: 7B-AWQ 5 min, 14B 10 min, 32B 20 min, 72B 40-60 min with one run each. If a model failed cell 8, leave it out, e.g. `run("all", "--backend", BACKEND, "--models", "qwen2.5-14b,qwen2.5-32b-int8", "--main-runs", SCALE_RUNS)`."""),
("code", """run("all", "--backend", BACKEND, "--models", SCALE, "--main-runs", SCALE_RUNS, "--smoke-first")"""),
("md", "## 10. Report and download"),
("code", """show_report()
download_results()"""),
("md", """---
# Part C: Llama 3.1 (after Meta approves)

You will get an e-mail from Hugging Face, and the model page (<https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct>) will say *You have been granted access*. The `HF_TOKEN` secret must be set for this notebook (key icon, **Notebook access** on); if you added it after running cell 2, run cell 2 again.

In a new session: run cells 1, 2 and 3 first, then continue here."""),
("md", "## 11. Has Meta approved it yet?\n\nTakes a second. Prints `OK, can be downloaded`, or what is still missing."),
("code", """run("access", "--models", LATER)"""),
("md", "## 12. Smoke test Llama"),
("code", """run("smoke", "--backend", BACKEND, "--models", LATER)"""),
("md", """## 13. Main runs for Llama (about 30-40 min)

Only Llama's episodes run. Earlier results are restored from Drive and kept, and the report is rebuilt over all models."""),
("code", """run("all", "--backend", BACKEND, "--models", LATER, "--main-runs", MAIN_RUNS)"""),
("md", "## 14. Final report and download"),
("code", """show_report()
download_results()"""),
("md", """## Troubleshooting

| Symptom | Fix |
|---|---|
| Cell 2: `Upload storageguard_colab.zip to ... first` | The zip is not in `MyDrive/storageguard/`, or `DRIVE_DIR` points elsewhere. |
| Session disconnected | Run cells 1, 2, 3, then the cell that was running. Nothing finished is lost. |
| A smoke test fails | The output lists the problems and shows replies the model wrote without a tool call. Leave that model out and report it as a limitation. |
| Cell 11: `NO ACCESS` | Not approved yet (the model page says "awaiting review"), or the token is fine-grained without "Read access to contents of all public gated repos": use a classic *Read* token, update the secret, run cell 2 again. |
| vLLM install fails or the server exits at start | Read the log tail printed above. Setup installs the pinned vLLM 0.33.0 with matching packages; keep it for the whole study. Last resort: `BACKEND = "ollama"` (results stored as `<model>-q4`, never mixed with vLLM ones). |
| Mistral-format model: `400 ... missing ... 'force_tool_names'` | mistral_common too new for vLLM 0.33.0: run cell 3 again with the current zip, or `!uv pip install --python /content/venv-vllm/bin/python "mistral-common[image]==1.12.0"`. |
| CUDA out of memory | Add `"--concurrency", 8` to the `run(...)` call. |
| Want to check progress | `!find results/runs -name '*.json' | wc -l` in a new cell. |"""),
]

SCALE_CELLS = [
("md", """# StorageGuard: scale study (larger models)

Run this after the main study (Qwen2.5-7B and Ministral 3 8B). It asks: **does a larger model still need the guard?** Same family (Qwen2.5), same tool format, increasing size, the five main configurations on the 104 test tasks.

| Model | Weights | Why this precision |
|---|---|---|
| Qwen2.5-7B (done) | BF16 | main study |
| **Qwen2.5-7B AWQ** | 4-bit, 5.6 GB | control: what 4-bit weights alone change |
| **Qwen2.5-14B** | BF16, 29.5 GB | full precision |
| **Qwen2.5-32B GPTQ-Int8** | 8-bit, 35.1 GB | official, near-lossless; BF16 (65.5 GB) leaves no KV cache |
| **Qwen2.5-72B AWQ** | 4-bit, 41.6 GB | official; 8-bit (77.1 GB) does not fit |

On a 40 GB A100, cell 2 switches to the 40 GB ladder (14B 8-bit, 32B 4-bit) by itself.

| Cells | What | Time on an A100 80 GB (estimate) |
|---|---|---|
| 1-3 | GPU check, Drive, install (a new runtime starts empty) | ~10 min |
| 4 | per model: download, smoke test, main runs (1 run each) | ~1.5-2 h (~3.5 h with `SCALE_RUNS = 3`) |
| 5-6 | report over all models so far, download | 2 min |
| 7-9 | optional: Llama 3.1, if Meta has approved it by now | ~30 min |

**Before you start**

1. If another notebook is still connected, stop its runtime there (**Runtime -> Disconnect and delete runtime**), so two A100s are not billed at once.
2. Upload the newest `storageguard_colab.zip` into `MyDrive/storageguard/` (**Replace existing file**). Your earlier results are in `MyDrive/storageguard/state/` and are restored automatically; nothing finished is run again.
3. **Runtime -> Change runtime type -> A100 GPU**. The `HF_TOKEN` secret is only needed for the Llama section."""),
("md", "## 1. Check the GPU"),
("code", GPU_CHECK),
("md", "## 2. Settings, Google Drive and the project"),
("code", """# ---- settings -------------------------------------------------------------
BACKEND    = "vllm"
SCALE      = "auto"      # "auto" = the ladder for this GPU; or a list, e.g. "qwen2.5-14b,qwen2.5-32b-int8"
SCALE_RUNS = 1           # runs per configuration; 3 matches the main models (about twice as long overall)
LLAMA      = "llama3.1-8b"                          # optional last section, once Meta has approved it
DRIVE_DIR  = "/content/drive/MyDrive/storageguard"  # where storageguard_colab.zip is
# ---------------------------------------------------------------------------
LADDER_80GB = "qwen2.5-7b-awq,qwen2.5-14b,qwen2.5-32b-int8,qwen2.5-72b-awq"
LADDER_40GB = "qwen2.5-7b-awq,qwen2.5-14b-int8,qwen2.5-32b-awq"
if SCALE == "auto":
    import subprocess
    try:
        _mib = float(subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True).stdout.split()[0])
    except Exception:
        _mib = 0.0
    SCALE = LADDER_80GB if _mib >= 70000 else LADDER_40GB
    print(f"GPU memory {_mib / 1024:.0f} GB -> scale ladder: {SCALE}")
""" + "\n" + PREAMBLE),
("md", INSTALL_MD),
("code", """run("setup", "--backend", BACKEND)"""),
("md", """## 4. Scale study: smoke test and main runs, model by model

Smallest model first. For each model: check GPU memory and download access (seconds), download (when the disk runs short, cached weights of finished models are deleted), smoke test, then the main runs only if the smoke test passed. A model that fails is reported and skipped; the others continue. At the end the report is rebuilt over all models.

Keep the tab open. If the session disconnects: run cells 1, 2, 3, then this cell again; finished episodes are skipped."""),
("code", """run("all", "--backend", BACKEND, "--models", SCALE, "--main-runs", SCALE_RUNS, "--smoke-first")"""),
("md", "## 5. Report (all models so far)"),
("code", "show_report()"),
("md", "## 6. Download the results\n\nA copy is also saved in `MyDrive/storageguard/`."),
("code", "download_results()"),
("md", """---
## Optional: Llama 3.1 (only once Meta has approved it)

The model page (<https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct>) must say *You have been granted access*, and the `HF_TOKEN` secret must have **Notebook access** (if you add it now, run cell 2 again).

## 7. Has Meta approved it yet? (seconds)"""),
("code", """run("access", "--models", LLAMA)"""),
("md", "## 8. Llama: smoke test and main runs (3 runs, like the other main models; ~30 min)"),
("code", """run("all", "--backend", BACKEND, "--models", LLAMA, "--main-runs", 3, "--smoke-first")"""),
("md", "## 9. Final report and download"),
("code", """show_report()
download_results()"""),
("md", """## Troubleshooting

| Symptom | Fix |
|---|---|
| Cell 4 stops at once: `needs a ... GB+ GPU` | This runtime has a smaller GPU (e.g. a 40 GB A100): set `SCALE = "auto"` (or the 40 GB ladder) in cell 2 and run cells 2 and 4 again. |
| A model: `SMOKE FAIL` | Its runs are skipped and the others continue. The output shows what the model wrote; report it as a limitation. |
| `CUDA out of memory` for the 72B model | Run cell 4 again with `"--concurrency", 4` added: finished models are skipped. |
| Session disconnected | Run cells 1, 2, 3, then the cell that was running. Nothing finished is lost. |
| Download fails: no space left on device | Run the cell again: finished models' cached weights are removed first. |
| Want to check progress | `!find results/runs/main -name '*.json' | wc -l` in a new cell. |"""),
]

LLAMA_CELLS = [
("md", """# StorageGuard: finish Llama 3.1 8B

Re-runs only the 240 Llama episodes that crashed (DECISIONS.md, C15); everything finished is restored from Drive and skipped.

Before you start: upload the new `storageguard_colab.zip` to `MyDrive/storageguard/` (**Replace existing file**), pick **Runtime -> Change runtime type -> A100 GPU**, then **Runtime -> Run all** (about 30-40 min)."""),
("code", GPU_CHECK),
("code", """BACKEND   = "vllm"
LLAMA     = "llama3.1-8b"
DRIVE_DIR = "/content/drive/MyDrive/storageguard"   # where storageguard_colab.zip is
""" + "\n" + PREAMBLE),
("code", """run("setup", "--backend", BACKEND)"""),
("code", """run("all", "--backend", BACKEND, "--models", LLAMA, "--main-runs", 3, "--smoke-first")"""),
("code", """show_report()
download_results()"""),
]

POSTHOC_CELLS = [
("md", """# StorageGuard: post-hoc variant P1, live (optional)

The physical replay found one harm that StorageGuard (B4) lets through: deleting a volume's *newest* snapshots one
call at a time while older ones remain (recovery-point erosion). P1 adds one check for it, tuned on the dev split
only (DECISIONS.md, section 11). This notebook runs the three main-study models under P1 on the test split
(3 runs, like B4), so each P1 episode pairs with the recorded B4 episode. It is reported **separately**; the
published B4 numbers are not changed.

Before you start: upload the new `storageguard_colab.zip` to `MyDrive/storageguard/` (**Replace existing file**),
pick **Runtime -> Change runtime type -> A100 GPU**, add the `HF_TOKEN` secret (Llama is gated), then
**Runtime -> Run all** (about 1-1.5 h)."""),
("code", GPU_CHECK),
("code", """BACKEND   = "vllm"
MODELS    = "qwen2.5-7b,llama3.1-8b,ministral-3-8b"
DRIVE_DIR = "/content/drive/MyDrive/storageguard"   # where storageguard_colab.zip is
""" + "\n" + PREAMBLE),
("code", """run("setup", "--backend", BACKEND)"""),
("code", """run("posthoc", "--backend", BACKEND, "--models", MODELS)"""),
("code", """print(open("results/posthoc/REPORT.md").read())
download_results()"""),
]

NOTEBOOKS = {"StorageGuard_Colab.ipynb": CELLS, "StorageGuard_Colab_Continue.ipynb": CONTINUE_CELLS,
             "StorageGuard_Colab_Scale.ipynb": SCALE_CELLS, "StorageGuard_Colab_Llama.ipynb": LLAMA_CELLS,
             "StorageGuard_Colab_Posthoc.ipynb": POSTHOC_CELLS}


def build(cell_specs: list | None = None) -> dict:
    cells = []
    for kind, src in cell_specs or CELLS:
        lines = src.splitlines(keepends=True)
        if kind == "md":
            cells.append({"cell_type": "markdown", "metadata": {}, "source": lines})
        else:
            cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": lines})
    return {
        "cells": cells,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"gpuType": "A100", "machine_shape": "hm", "provenance": []},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 0,
    }


if __name__ == "__main__":
    for name, specs in NOTEBOOKS.items():
        out = Path(__file__).resolve().parents[1] / "colab" / name
        out.write_text(json.dumps(build(specs), indent=1) + "\n")
        print("wrote", out)
