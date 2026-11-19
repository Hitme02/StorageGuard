# Running the experiments on Google Colab (A100)

Two files go to Google Drive, one notebook runs everything, and results come back as one zip.

| What | Time |
|---|---|
| One-time preparation (Hugging Face, Drive upload) | 15 min |
| Notebook cells 1-3: GPU check, Drive, install | about 10 min |
| Notebook cell 4: smoke test (also downloads the three models, ~15 GB each) | about 20-30 min |
| Notebook cell 5: full run (dev + main 3 models x 5 configs x 3 runs + ablations + report) | about 2-4 h (estimate) |
| Compute units (A100, roughly 12 units/hour; check current rates) | about 35-60 units, within one Colab Pro month |

The free Colab tier does not offer A100s; you need Colab Pro, Pro+ or pay-as-you-go compute units.

---

## Step 1. Build the upload bundle (on your Mac)

```bash
cd storageguard
make colab-bundle
```

This writes three files to `dist/`:

- `dist/storageguard_colab.zip` (the project: code, configs, the 150-task benchmark, tests)
- `dist/StorageGuard_Colab.ipynb` (the notebook for the first session)
- `dist/StorageGuard_Colab_Continue.ipynb` (the notebook for every later session; see "Later sessions" below)

Rebuild the bundle whenever you change the code.

## Step 2. Hugging Face access (only for Llama)

Qwen and Ministral 3 download without an account. Llama 3.1 is *gated* with manual approval by Meta.

1. Create or log in to an account at <https://huggingface.co>.
2. Open <https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct>, fill in the licence form and submit. Meta approves requests by hand: usually within hours, sometimes longer, so do this before you start a paid runtime. The page shows "You have been granted access" when done; until then downloads fail with `403 ... not in the authorized list`.
4. Avatar menu -> **Settings** -> **Access Tokens** -> **Create new token** -> type **Read** -> create, and copy the token (`hf_...`). (A *fine-grained* token also works only if "Read access to contents of all public gated repos you can access" is ticked.)

## Step 3. Upload to Google Drive

1. Go to <https://drive.google.com>, **My Drive** -> **New** -> **New folder**, name it exactly `storageguard`.
2. Open the folder, **New** -> **File upload**, and select both files from `dist/`.

The folder must look like this (the notebook expects this path):

```
My Drive/
  storageguard/
    storageguard_colab.zip
    StorageGuard_Colab.ipynb
```

## Step 4. Open the notebook on an A100

1. In Drive, double-click `StorageGuard_Colab.ipynb` -> **Open with** -> **Google Colaboratory**. (If Colaboratory is not listed: **Open with** -> **Connect more apps** -> search "Colaboratory" -> install.)
2. Menu **Runtime** -> **Change runtime type** -> Hardware accelerator **A100 GPU** -> **Save**. Select High-RAM if offered.
3. Left sidebar, **key icon (Secrets)** -> **Add new secret**: Name `HF_TOKEN`, Value your `hf_...` token, and switch **Notebook access** on.

## Step 5. Run the cells in order

Run each cell with the play button (or Shift+Enter) and wait for it to finish before starting the next one.

| Cell | What it does | You should see |
|---|---|---|
| **1. Check the GPU** | prints the GPU | `NVIDIA A100-SXM4-40GB, 40960 MiB, ...` |
| **2. Settings** | mounts Drive (approve the pop-up), unpacks the project, loads the token | `HF_TOKEN loaded` and `project ready at /content/storageguard` |
| **3. Install** | two isolated environments (harness, vLLM) and the fast unit tests | ends with `setup complete` |
| **4. Smoke test** | checks Hugging Face access for every model (seconds), then for each model: start vLLM, run 3 trap tasks x 4 configs (12 episodes), check tool calls, guard, judge and `finish`. A failing model does not stop the others | `SMOKE PASS for ...` per model, a `smoke summary`, then `smoke test PASSED for all models` |
| **5. Full run** | dev split, main matrix, ablations, then the report | progress lines like `120/1575  1.2s/episode  eta 30 min` |
| **6. Report** | shows `REPORT.md` and the six figures | tables and figures |
| **7. Download** | zips everything to Drive and downloads it | `storageguard_results_<date>.zip` |

**Do not start cell 5 with a model that did not pass cell 4.** If only some models passed, put just those in `MODELS` (cell 2) and add the others later (next section).

Keep the browser tab open during cell 5. Colab may disconnect idle sessions; see Step 7.

### Settings you may change (top of cell 2)

| Setting | Default | Meaning |
|---|---|---|
| `BACKEND` | `"vllm"` | `"ollama"` is the fallback if vLLM will not install or start |
| `MODELS` | `"qwen2.5-7b,llama3.1-8b,ministral-3-8b"` | the scale study adds `qwen2.5-7b-awq,qwen2.5-14b,qwen2.5-32b-int8,qwen2.5-72b-awq` on an 80 GB A100 (see the Continue notebook, Part B) |
| `MAIN_RUNS` | `3` | runs per (model, config); `1` cuts cell 5 to roughly a third |
| `DRIVE_DIR` | `/content/drive/MyDrive/storageguard` | where you uploaded the files |

### Later sessions: `StorageGuard_Colab_Continue.ipynb`

Use this notebook for everything after the first session (it is set up for the current plan: Qwen and Mistral now, Llama once Meta approves it).

1. Stop the first notebook's runtime if it is still connected (**Runtime -> Disconnect and delete runtime**), so two A100s are not billed at once.
2. Upload the new `storageguard_colab.zip` and `StorageGuard_Colab_Continue.ipynb` to `My Drive/storageguard/`. When Drive asks about the zip, choose **Replace existing file** (cell 2 uses the newest zip either way).
3. Open `StorageGuard_Colab_Continue.ipynb` with Colaboratory, select the A100 runtime, check the `HF_TOKEN` secret has **Notebook access** for this notebook too.
4. **Part A** (cells 1-7, about 2-3 h): install, smoke test Ministral 3 8B, full run for Qwen (dev, main, ablations) and Ministral (main), report, download the interim results.
5. **Part B** (cells 8-10, about 1.5-2 h, recommended): the scale study on an 80 GB A100. Qwen2.5 14B (full precision), 32B (official 8-bit) and 72B (official 4-bit), plus the 7B in 4-bit as a quantisation control; main matrix, one run each (`SCALE_RUNS = 3` matches Part A, about 3.5-4 h). On a 40 GB A100 use the line given in cell 2 (14B 8-bit, 32B 4-bit). The driver frees disk space by deleting cached weights of finished models when needed.
6. **Part C** (cells 11-14, about 45 min), once the Llama page says *You have been granted access*: cell 11 checks access in a second, then smoke test, Llama's main runs, final report and download.

Parts B and C are independent. In a new session run cells 1-3 first.

Why not Mistral-7B-Instruct-v0.3: on the A100 it made 0 tool calls in 20 steps. In its own chat format it writes command lines (`get_volume arch-a8c4`) instead of tool calls (DECISIONS.md, C8). Ministral 3 8B is Mistral's current model of that size, with native function calling.

### Scale study: `StorageGuard_Colab_Scale.ipynb`

A self-contained notebook for the larger models, to run after the main study:

1. Stop any other connected runtime, upload the newest `storageguard_colab.zip` (Replace existing file) and `StorageGuard_Colab_Scale.ipynb` to `My Drive/storageguard/`, open the notebook on an A100.
2. Cells 1-3: GPU check, Drive, install (~10 min). Cell 2 picks the ladder for the GPU: 7B-AWQ, 14B, 32B-Int8 and 72B-AWQ on 80 GB; 7B-AWQ, 14B-Int8 and 32B-AWQ on 40 GB.
3. Cell 4 (~1.5-2 h with one run each): for each model, smallest first, one download and one load: smoke test, then the main runs only if it passed. Then the report over all models.
4. Cells 5-6: report and download. Cells 7-9 (optional): Llama 3.1, once Meta has approved it.

Earlier results are restored from Drive and never re-run.

### Adding a model by hand

Results of finished models are kept, so a model can join at any time, in the same session or a new one (after cells 1-3):

```python
run("smoke", "--backend", BACKEND, "--models", "llama3.1-8b")
run("all", "--backend", BACKEND, "--models", "llama3.1-8b", "--main-runs", MAIN_RUNS)
```

Only that model's episodes run; the report is then rebuilt over all models.

## Step 6. Bring the results back

1. Cell 7 downloads `storageguard_results_<date>.zip` (a copy also stays in the Drive folder).
2. On the Mac, unzip it **inside the project folder** so `results/`, `paper/`, `data/bench/` and `configs/` are overwritten:

   ```bash
   cd storageguard
   unzip -o ~/Downloads/storageguard_results_*.zip
   venv/bin/sg report        # optional: rebuild the report locally
   ```

3. The paper material is in `results/REPORT.md`, `paper/figures/` (PDF and PNG), `paper/tables/` (LaTeX), `results/case_studies/` and `results/episodes.csv`.

The earlier Mac (Ollama) pilot runs are kept separately in `results/pilot_mac/` and are not used by the report.

## Step 7. If the session disconnects

Nothing finished is lost: every episode is saved as it completes and mirrored to `My Drive/storageguard/state/` every 5 minutes.

1. Reconnect (or **Runtime** -> **Change runtime type** -> A100 for a fresh runtime).
2. Run cells **1, 2 and 3** again (a fresh runtime has to reinstall; about 10 min).
3. Run cell **5** again. It restores the saved results from Drive and skips every finished episode. The smoke test does not need to be repeated.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Cell 1: no GPU / not an A100 | Runtime -> Change runtime type -> A100 GPU. If A100 is unavailable, an L4 (24 GB) also works for the 7-8B models, only slower. |
| Cell 2: `Upload storageguard_colab.zip to ... first` | The folder name or location in Drive differs; fix it or change `DRIVE_DIR`. |
| Cell 2: `No HF_TOKEN` | Secret missing or **Notebook access** is off. Qwen still works. |
| Cell 4 or 5: `403`, "not in the authorized list" for Llama/Mistral | The account behind `HF_TOKEN` has no access yet. Open the model page logged in: submit the form if shown, or wait if it says "awaiting review". If it says "granted", the token is fine-grained without gated-repo access: make a classic Read token, update the secret, re-run cell 2. Meanwhile run the other models (see "Adding a model later"). |
| Cell 4 or 5: `401` for Llama/Mistral | No token or a rejected one: check the `HF_TOKEN` secret (Notebook access on), re-run cell 2. |
| Cell 3 or 4: vLLM fails to install or the server exits at start | The last log lines are printed. Setup installs vLLM 0.33.0 with matching companion packages (DECISIONS.md, C11); keep that version for the whole study. If it still fails, set `BACKEND = "ollama"` and re-run cells 2-5. Ollama results are stored as `<model>-q4` and never mixed with vLLM results. |
| A Mistral-format model: `400 ... get_lark_from_jinja() missing ... 'force_tool_names'` | mistral_common is too new for vLLM 0.33.0. Run cell 3 again with the current zip (it pins mistral_common 1.12.0), or in a new cell: `!uv pip install --python /content/venv-vllm/bin/python "mistral-common[image]==1.12.0"`. |
| Cell 4: `never produced a valid tool call` for one model | The model's tool-call format does not match vLLM's parser. Remove that model from `MODELS` (report it as a limitation) or try `BACKEND = "ollama"`. |
| CUDA out of memory | Add `"--concurrency", 8` to the `run(...)` call of the failing cell. |
| Progress check during cell 5 | Open a new cell: `!find results/runs -name '*.json' \| wc -l` |

## What was tested before handing this over, and what was not

Tested on the Mac:

- the full unit-test suite on Python 3.11 and 3.12 (Colab's Python);
- the driver end to end against a fake OpenAI-compatible server: server start and readiness, 4 configurations in parallel, the smoke checks, a full `all` rehearsal (dev, main for two models, ablations, report with all 6 figures), the Drive mirroring and the results zip;
- the notebook itself, cell by cell, with `google.colab` stubbed out and the Ollama backend serving a real local model: cells 1-3, the smoke test (8 episodes, passed), cell 5 through the scripted stage, the dev split and the first main runs (stopped on purpose after 40 min; a Mac is far slower than an A100), then the report, the Drive mirror and the results zip;
- the notebook format and every code cell's syntax.

Not tested before the first Colab session: vLLM on an A100 (vLLM cannot run on a Mac GPU). Its flags were taken from the current vLLM documentation (tool calling: `hermes` for Qwen, `llama3_json` plus the Llama 3.1 JSON chat template, `mistral` with Mistral's own tokenizer format). Notebook cell 4 is the gate for this: it fails loudly, with the server log, before any long run starts.

Confirmed on the first A100 session: vLLM installs and serves Qwen2.5-7B with the `hermes` tool parser; its smoke test passed (12 episodes, 64 LLM steps, 2 malformed, server ready in 145 s including the download).

Results from vLLM (full-precision weights) are not directly comparable with the Mac pilot (4-bit Ollama weights). The paper should use one backend throughout, which this notebook does. Threshold tuning used scripted plans without a model, so it carries over unchanged.
