#!/usr/bin/env python3
"""Run the StorageGuard experiments on a single cloud GPU (Google Colab A100).

The driver serves one model at a time with vLLM (default) or Ollama (fallback),
runs the experiment stages against it, and mirrors results to Google Drive so a
disconnected session can resume where it stopped.

    python colab/sg_colab.py setup                 # install the harness (+ vLLM or Ollama)
    python colab/sg_colab.py access --models llama3.1-8b   # can the HF token download it yet?
    python colab/sg_colab.py smoke                 # short end-to-end check, every model
    python colab/sg_colab.py all                   # dev + main + ablations, then the report
    python colab/sg_colab.py report                # rebuild figures, tables, REPORT.md
    python colab/sg_colab.py pack                  # zip the artefacts to Drive

It only uses the standard library, so it runs before anything is installed.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SG_VENV = Path(os.environ.get("SG_VENV", "/content/venv-sg"))
VLLM_VENV = Path(os.environ.get("SG_VLLM_VENV", "/content/venv-vllm"))
DRIVE_STATE = Path(os.environ.get("SG_DRIVE_STATE", "/content/drive/MyDrive/storageguard/state"))
MODELS_FILE = ROOT / "configs" / "models_colab.yaml"
# vLLM is pinned so every model in the study runs on the same server version (reported in the paper).
DEFAULT_VLLM = "0.33.0"
# Chat templates ship in colab/templates/ (copied unmodified from this vLLM release); the URL is only a fallback.
TEMPLATE_URL = f"https://raw.githubusercontent.com/vllm-project/vllm/v{DEFAULT_VLLM}/examples/{{}}"
TEMPLATE_SHA256 = {"tool_chat_template_llama3.1_json.jinja": "8aedd05f7ab549b897b929c89da9b62e537f4f4197f33736e9d2f10fea53b1bf"}
# Packages that must match a vLLM version. vLLM 0.33.0 requires only mistral_common>=1.11.6, but calls its grammar
# API without `force_tool_names`, which mistral_common 1.13.0 made mandatory: every Mistral-format request with tools
# then fails with HTTP 400. 1.12.0 is the last release with the API vLLM 0.33.0 uses.
COMPANION_PINS = {"0.33.0": ["mistral-common[image]==1.12.0"]}
VLLM_PORT, OLLAMA_PORT = 8000, 11434


@dataclass(frozen=True)
class ModelInfo:
    name: str  # short name used in results and the paper
    hf_id: str
    parser: str  # vLLM --tool-call-parser
    ollama_tag: str
    template: str | None = None  # vLLM examples/ chat template, downloaded on demand
    mistral_format: bool = False
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    max_concurrency: int = 16  # larger models hold fewer sequences in the KV cache; more would only queue or preempt
    size_gb: float = 16.0  # download size of the weights vLLM loads (disk management)
    min_gpu_gb: int = 24  # smallest GPU on which weights + a 32k-token KV cache fit
    single_tool_calls: bool = False  # chat template takes one tool call per assistant message (DECISIONS.md, C15)


AWQ = ("--quantization", "awq_marlin")
MODELS: dict[str, ModelInfo] = {
    "qwen2.5-7b": ModelInfo("qwen2.5-7b", "Qwen/Qwen2.5-7B-Instruct", "hermes", "qwen2.5:7b", size_gb=15.2),
    "llama3.1-8b": ModelInfo("llama3.1-8b", "meta-llama/Llama-3.1-8B-Instruct", "llama3_json", "llama3.1:8b",
                             template="tool_chat_template_llama3.1_json.jinja", size_gb=16.1, single_tool_calls=True),
    # Mistral's model for the study: native function calling, Apache-2.0, BF16 weights
    "ministral-3-8b": ModelInfo("ministral-3-8b", "mistralai/Ministral-3-8B-Instruct-2512-BF16", "mistral", "ministral-3:8b",
                                mistral_format=True, size_gb=35.6),  # consolidated + sharded copies may both download
    # excluded: in its own chat format it writes CLI-style lines instead of tool calls (0/20 in the A100 smoke test)
    "mistral-7b": ModelInfo("mistral-7b", "mistralai/Mistral-7B-Instruct-v0.3", "mistral", "mistral:7b", mistral_format=True,
                            size_gb=14.5),
    # scale study on an 80 GB A100: one family, official checkpoints, the least compression that leaves a usable KV cache
    "qwen2.5-14b": ModelInfo("qwen2.5-14b", "Qwen/Qwen2.5-14B-Instruct", "hermes", "qwen2.5:14b", size_gb=29.5, min_gpu_gb=48),
    "qwen2.5-32b-int8": ModelInfo("qwen2.5-32b-int8", "Qwen/Qwen2.5-32B-Instruct-GPTQ-Int8", "hermes", "qwen2.5:32b",
                                  max_concurrency=12, size_gb=35.1, min_gpu_gb=64),
    "qwen2.5-72b-awq": ModelInfo("qwen2.5-72b-awq", "Qwen/Qwen2.5-72B-Instruct-AWQ", "hermes", "qwen2.5:72b",
                                 extra_args=AWQ, max_concurrency=8, size_gb=41.6, min_gpu_gb=80),
    # quantisation control: the 7B model in the 72B model's 4-bit format
    "qwen2.5-7b-awq": ModelInfo("qwen2.5-7b-awq", "Qwen/Qwen2.5-7B-Instruct-AWQ", "hermes", "qwen2.5:7b", extra_args=AWQ,
                                size_gb=5.6),
    # the same ladder on a 40 GB A100 (more compression)
    "qwen2.5-14b-int8": ModelInfo("qwen2.5-14b-int8", "Qwen/Qwen2.5-14B-Instruct-GPTQ-Int8", "hermes", "qwen2.5:14b",
                                  max_concurrency=12, size_gb=16.6, min_gpu_gb=40),
    "qwen2.5-32b-awq": ModelInfo("qwen2.5-32b-awq", "Qwen/Qwen2.5-32B-Instruct-AWQ", "hermes", "qwen2.5:32b", extra_args=AWQ,
                                 max_concurrency=8, size_gb=19.3, min_gpu_gb=40),
}  # fmt: skip
PRIMARY = "qwen2.5-7b"
DEFAULT_MODELS = ["qwen2.5-7b", "llama3.1-8b", "ministral-3-8b"]
SCALE_MODELS = ["qwen2.5-7b-awq", "qwen2.5-14b", "qwen2.5-32b-int8", "qwen2.5-72b-awq"]  # 80 GB A100
SCALE_MODELS_40GB = ["qwen2.5-7b-awq", "qwen2.5-14b-int8", "qwen2.5-32b-awq"]


def log(msg: str) -> None:
    print(f"[sg-colab {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sh(cmd: list[str] | str, check: bool = True, env: dict | None = None, cwd: Path | None = None) -> int:
    """Run a command, streaming its output."""
    shown = cmd if isinstance(cmd, str) else " ".join(cmd)
    log(f"$ {shown}")
    rc = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd or ROOT, env={**os.environ, **(env or {})}).returncode
    if check and rc != 0:
        raise SystemExit(f"command failed ({rc}): {shown}")
    return rc


def served_name(m: ModelInfo, backend: str) -> str:
    """Results are stored per short name; the Ollama fallback gets its own name so backends never mix."""
    return m.name if backend == "vllm" else f"{m.name}-q4"


# --------------------------------------------------------------------------- #
# setup
# --------------------------------------------------------------------------- #


def vllm_requirements(version: str = "") -> list[str]:
    """pip requirements for the vLLM environment: the pinned version (or ``latest``) plus its companion pins."""
    version = version or DEFAULT_VLLM
    if version == "latest":
        return ["vllm"]
    return [f"vllm=={version}", *COMPANION_PINS.get(version, [])]


def record_environment(uv: str = "uv") -> Path:
    """Write the exact package versions of both environments and the GPU into results/environment (for the paper)."""
    out = ROOT / "results" / "environment"
    out.mkdir(parents=True, exist_ok=True)
    for name, venv in (("harness", SG_VENV), ("vllm", VLLM_VENV)):
        if (venv / "bin" / "python").exists():
            r = subprocess.run([uv, "pip", "freeze", "--python", str(venv / "bin" / "python")], capture_output=True, text=True)
            (out / f"{name}_packages.txt").write_text(r.stdout)
    with contextlib.suppress(OSError):
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv"], capture_output=True, text=True)
        (out / "gpu.txt").write_text(r.stdout)
    return out


def setup(backend: str, vllm_version: str = "") -> None:
    if shutil.which("uv") is None:
        sh([sys.executable, "-m", "pip", "install", "-q", "uv"])
    uv = shutil.which("uv") or "uv"
    if not (SG_VENV / "bin" / "python").exists():
        sh([uv, "venv", "--python", "3.11", str(SG_VENV)])
    sh([uv, "pip", "install", "--python", str(SG_VENV / "bin" / "python"), "-e", f"{ROOT}[dev]"])
    if backend == "vllm":
        if not (VLLM_VENV / "bin" / "python").exists():
            sh([uv, "venv", "--python", "3.12", str(VLLM_VENV)])
        sh([uv, "pip", "install", "--python", str(VLLM_VENV / "bin" / "python"), *vllm_requirements(vllm_version), "--torch-backend=auto"])
        vllm = VLLM_VENV / "bin" / "vllm"
        if sh([str(vllm), "download-kernels"], check=False) != 0:
            log("note: `vllm download-kernels` is not available in this vLLM version; continuing")
        sh([str(vllm), "--version"], check=False)
    else:
        if shutil.which("ollama") is None:
            sh("apt-get -qq update && apt-get -qq install -y zstd pciutils >/dev/null")
            sh("curl -fsSL https://ollama.com/install.sh | sh")
    sh([str(SG_VENV / "bin" / "pytest"), "-q", "-x", "tests/test_twin.py", "tests/test_guard.py", "tests/test_oracle.py"])
    log(f"package versions recorded in {record_environment(uv)}")
    log("setup complete")


# --------------------------------------------------------------------------- #
# model serving
# --------------------------------------------------------------------------- #


def vllm_command(m: ModelInfo, port: int = VLLM_PORT, max_len: int = 32768, template_dir: Path | None = None) -> list[str]:
    cmd = [
        str(VLLM_VENV / "bin" / "vllm"), "serve", m.hf_id, "--served-model-name", m.hf_id,
        "--host", "127.0.0.1", "--port", str(port), "--max-model-len", str(max_len),
        "--gpu-memory-utilization", "0.90", "--enable-auto-tool-choice", "--tool-call-parser", m.parser,
        "--generation-config", "vllm", "--enable-prefix-caching", "--seed", "0",
    ]  # fmt: skip
    if m.template:
        cmd += ["--chat-template", str((template_dir or ROOT / "colab" / "templates") / m.template)]
    if m.mistral_format:
        cmd += ["--tokenizer-mode", "mistral", "--config-format", "mistral", "--load-format", "mistral"]
    return cmd + list(m.extra_args)


def fetch_template(name: str, dest_dir: Path) -> Path:
    """The chat template ``name``: the copy in ``dest_dir`` (shipped with the repo), downloaded only if missing.
    Its SHA-256 must match the pinned release, so a run can never pick up a changed template silently."""
    dest = dest_dir / name
    if not dest.exists():
        dest_dir.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(TEMPLATE_URL.format(name), dest)
    want = TEMPLATE_SHA256.get(name)
    got = hashlib.sha256(dest.read_bytes()).hexdigest()
    if want and got != want:
        raise SystemExit(f"chat template {dest} does not match vLLM v{DEFAULT_VLLM} (sha256 {got[:12]}..., expected {want[:12]}...)")
    return dest


def write_models_file(m: ModelInfo, backend: str, concurrency: int, path: Path | None = None) -> Path:
    """Model registry pointing the harness at the model being served right now."""
    if backend == "vllm":
        model, url = m.hf_id, f"http://127.0.0.1:{VLLM_PORT}/v1"
    else:
        model, url = f"sg-{m.ollama_tag}", f"http://127.0.0.1:{OLLAMA_PORT}/v1"
    body = {"models": {served_name(m, backend): {
        "backend": backend, "model": model, "base_url": url, "concurrency": min(concurrency, m.max_concurrency), "context_chars": 36000,
        **({"single_tool_call_turns": True} if m.single_tool_calls else {}),
    }}}  # fmt: skip
    path = path or MODELS_FILE
    path.write_text(json.dumps(body, indent=2))  # JSON is valid YAML
    return path


def wait_ready(url: str, proc: subprocess.Popen | None, log_path: Path | None, timeout: float = 1800) -> None:
    """Poll ``url`` until it answers 200; fail fast if the server process dies."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            tail = log_path.read_text()[-4000:] if log_path and log_path.exists() else ""
            raise SystemExit(f"model server exited with code {proc.returncode}. Last log lines:\n{tail}")
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    log(f"server ready after {time.time() - t0:.0f}s")
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(5)
    raise SystemExit(f"server at {url} not ready after {timeout:.0f}s")


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


@contextlib.contextmanager
def serve(m: ModelInfo, backend: str, concurrency: int, command: list[str] | None = None, ready_url: str | None = None) -> Iterator[None]:
    """Serve one model for the duration of the block. ``command``/``ready_url`` override for tests."""
    logs = ROOT / "results" / "server_logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{backend}-{m.name}.log"
    write_models_file(m, backend, concurrency)
    proc: subprocess.Popen | None = None
    try:
        if command is not None:
            proc = subprocess.Popen(command, stdout=log_path.open("w"), stderr=subprocess.STDOUT, start_new_session=True)
            wait_ready(ready_url or f"http://127.0.0.1:{VLLM_PORT}/v1/models", proc, log_path)
        elif backend == "vllm":
            ensure_disk(m)
            if m.template:
                fetch_template(m.template, ROOT / "colab" / "templates")
            cmd = vllm_command(m)
            log(f"starting vLLM for {m.hf_id} (first start downloads the weights; log: {log_path})")
            proc = subprocess.Popen(cmd, stdout=log_path.open("w"), stderr=subprocess.STDOUT, start_new_session=True,
                                    env={**os.environ, "VLLM_LOGGING_LEVEL": "WARNING"})  # fmt: skip
            wait_ready(f"http://127.0.0.1:{VLLM_PORT}/v1/models", proc, log_path)
        else:
            proc = ensure_ollama(m, concurrency, log_path)
        yield
    finally:
        stop(proc)
        if backend == "vllm":
            time.sleep(5)  # let the GPU memory be released before the next model loads


HF_FILE_URL = "https://huggingface.co/{}/resolve/main/config.json"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect to the file store already proves access; never forward the token to another host."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def hf_access(m: ModelInfo, token: str | None = None, url: str | None = None, timeout: float = 20) -> str | None:
    """Check in a second whether the Hugging Face token can download ``m`` (instead of finding out from a vLLM crash).

    Returns None when the model is reachable (or the check itself could not run), else what to do about it.
    """
    token = token if token is not None else (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "")
    req = urllib.request.Request(url or HF_FILE_URL.format(m.hf_id), method="HEAD")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout):
            return None
    except urllib.error.HTTPError as exc:
        code = exc.code
    except (urllib.error.URLError, OSError) as exc:
        log(f"note: could not check Hugging Face access for {m.hf_id} ({exc}); trying anyway")
        return None
    page = f"https://huggingface.co/{m.hf_id}"
    if code < 400:
        return None
    if code == 401 and not token:
        return f"{m.hf_id} is gated and no HF_TOKEN is set: add the HF_TOKEN secret (Notebook access on), then re-run cell 2"
    if code == 401:
        return f"HF_TOKEN was rejected for {m.hf_id} (expired or mistyped): create a new Read token, update the secret, re-run cell 2"
    if code == 403:
        return (f"the Hugging Face account behind HF_TOKEN has no access to {m.hf_id} yet. Open {page} while logged in: "
                "submit the licence form if it is shown, or wait for approval if it says 'awaiting review'. If it says "
                "'granted', the token is fine-grained without 'Read access to contents of all public gated repos you can "
                "access': use a classic Read token instead, update the secret and re-run cell 2")  # fmt: skip
    if code == 404:
        return f"{m.hf_id} was not found on Hugging Face"
    log(f"note: Hugging Face answered {code} for {m.hf_id}; trying anyway")
    return None


def gpu_memory_gb() -> float | None:
    """Memory of GPU 0 in GB, or None without nvidia-smi."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=30).stdout.split()  # fmt: skip
        return float(out[0]) / 1024 if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def preflight(models: list[str], backend: str, gpu_gb: float | None = None) -> dict[str, str]:
    """Models that cannot run here (GPU too small, or weights not downloadable), with the fix."""
    if backend != "vllm":
        return {}
    gpu_gb = gpu_memory_gb() if gpu_gb is None else gpu_gb
    blocked = {}
    for name in models:
        m = MODELS[name]
        if gpu_gb is not None and gpu_gb < m.min_gpu_gb - 2:  # nvidia-smi reports a little under the nominal size
            blocked[name] = (f"needs a {m.min_gpu_gb} GB+ GPU, this one has {gpu_gb:.0f} GB. On a 40 GB A100 use the "
                             f"40 GB ladder instead: {','.join(SCALE_MODELS_40GB)}")  # fmt: skip
            continue
        problem = hf_access(m)
        if problem:
            blocked[name] = problem
    return blocked


def hf_hub_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def _dir_gb(d: Path) -> float:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file() and not f.is_symlink()) / 1e9


def ensure_disk(m: ModelInfo, hub: Path | None = None, free_gb: float | None = None, margin_gb: float = 15) -> list[str]:
    """Make room for ``m``'s weights: delete *other* models' cached weights (largest first) if the disk is short.

    The whole study downloads ~170 GB, more than a Colab disk holds. Only ``models--*`` folders of the
    Hugging Face cache are touched; a deleted model is simply downloaded again if it is needed later.
    """
    hub = hub or hf_hub_dir()
    if not hub.exists():
        return []
    mine = "models--" + m.hf_id.replace("/", "--")
    have = _dir_gb(hub / mine) if (hub / mine).exists() else 0.0
    free = shutil.disk_usage(hub).free / 1e9 if free_gb is None else free_gb
    need = max(0.0, m.size_gb - have) + margin_gb
    removed: list[str] = []
    for d in sorted((d for d in hub.glob("models--*") if d.is_dir() and d.name != mine), key=_dir_gb, reverse=True):
        if free >= need:
            break
        size = _dir_gb(d)
        shutil.rmtree(d, ignore_errors=True)
        free += size
        removed.append(d.name)
        log(f"disk: removed cached weights {d.name} ({size:.0f} GB) to make room for {m.name}")
    if free < need:
        log(f"warning: only {free:.0f} GB free for {m.name} ({need:.0f} GB wanted); the download may fail")
    return removed


def access(models: list[str], backend: str) -> bool:
    """Print, per model, whether its weights can be downloaded now (e.g. "has Meta approved the Llama licence yet?")."""
    blocked = preflight(models, backend)
    for name in models:
        log(f"{name}: " + (f"NO ACCESS. {blocked[name]}" if name in blocked else "OK, can be downloaded"))
    return not blocked


def ensure_ollama(m: ModelInfo, concurrency: int, log_path: Path) -> subprocess.Popen | None:
    url = f"http://127.0.0.1:{OLLAMA_PORT}/api/tags"
    proc = None
    try:
        urllib.request.urlopen(url, timeout=3)
        log("using the Ollama server that is already running")
    except (urllib.error.URLError, OSError):
        env = {**os.environ, "OLLAMA_NUM_PARALLEL": str(concurrency), "OLLAMA_KEEP_ALIVE": "-1",
               "OLLAMA_HOST": f"127.0.0.1:{OLLAMA_PORT}"}  # fmt: skip
        proc = subprocess.Popen(["ollama", "serve"], stdout=log_path.open("w"), stderr=subprocess.STDOUT, env=env, start_new_session=True)
        wait_ready(url, proc, log_path, timeout=120)
    sh(["ollama", "pull", m.ollama_tag])
    modelfile = ROOT / "results" / "server_logs" / f"Modelfile-{m.name}"
    modelfile.write_text(f"FROM {m.ollama_tag}\nPARAMETER num_ctx 16384\n")
    sh(["ollama", "create", f"sg-{m.ollama_tag}", "-f", str(modelfile)])
    return proc


# --------------------------------------------------------------------------- #
# Drive mirroring (resume after a disconnect)
# --------------------------------------------------------------------------- #

SYNC_DIRS = ("results", "paper")


def sync_tree(src: Path, dst: Path) -> int:
    """Copy files that are new or changed (size/mtime) from ``src`` to ``dst``; returns the number copied."""
    if not src.exists():
        return 0
    n = 0
    for p in src.rglob("*"):
        if not p.is_file() or p.suffix == ".tmp":
            continue
        q = dst / p.relative_to(src)
        st = p.stat()
        if q.exists():
            qs = q.stat()
            if qs.st_size == st.st_size and int(qs.st_mtime) >= int(st.st_mtime):
                continue
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, q)
        n += 1
    return n


def backup(include_cache: bool = False) -> None:
    """Mirror results (and, between runs, the LLM cache) to Drive."""
    if not DRIVE_STATE.parent.exists():
        return
    n = sum(sync_tree(ROOT / d, DRIVE_STATE / d) for d in SYNC_DIRS)
    if include_cache:
        n += sync_tree(ROOT / ".cache", DRIVE_STATE / ".cache")
    log(f"backed up {n} changed files to {DRIVE_STATE}")


def restore() -> None:
    if not DRIVE_STATE.exists():
        return
    n = sum(sync_tree(DRIVE_STATE / d, ROOT / d) for d in (*SYNC_DIRS, ".cache"))
    log(f"restored {n} files from {DRIVE_STATE}")


class PeriodicBackup:
    """Back up results every few minutes while a run is in progress."""

    def __init__(self, every_s: int = 300) -> None:
        import threading

        self.every, self.stop_evt = every_s, threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self.stop_evt.wait(self.every):
            with contextlib.suppress(Exception):
                backup()

    def __enter__(self) -> PeriodicBackup:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop_evt.set()
        backup(include_cache=True)


# --------------------------------------------------------------------------- #
# stages
# --------------------------------------------------------------------------- #


def sg(*args: str, check: bool = True) -> int:
    env = {"SG_MODELS_FILE": str(MODELS_FILE), "PYTHONUNBUFFERED": "1", "MPLBACKEND": "Agg"}  # not the notebook's inline backend
    return sh([str(SG_VENV / "bin" / "sg"), *args], check=check, env=env)


SMOKE_CONFIGS = "noguard;rbac;llm_judge;storageguard"


def check_smoke(model: str, exp: str = "colab_smoke", min_episodes: int = 1, root: Path | None = None) -> tuple[bool, list[str]]:
    """Acceptance checks on the smoke episodes of one model."""
    root = (root or ROOT) / "results" / "runs" / exp / model.replace(":", "-").replace("/", "-")
    rows = [json.loads(p.read_text()) for p in root.rglob("*.json")]
    problems: list[str] = []
    if len(rows) < min_episodes:
        problems.append(f"only {len(rows)} episodes finished (expected {min_episodes})")
    if rows and all(r["termination"] == "no_tool_call" for r in rows):
        problems.append("the model never produced a valid tool call (check the tool-call parser / chat template)")
    if rows and sum(r["n_mutating"] for r in rows) == 0:
        problems.append("no mutating tool call reached the guard in any episode")
    if rows and sum(r["finished"] for r in rows) == 0:
        problems.append("no episode ended with `finish`")
    calls = sum(r["steps"] for r in rows)
    errs = sum(r["errors"] for r in rows)
    if calls and errs / calls > 0.5:
        problems.append(f"{errs}/{calls} steps were malformed tool calls")
    judge = [r for r in rows if r["config"] == "llm_judge"]
    if judge and sum(r["extra_llm_calls"] for r in judge) == 0 and sum(r["n_mutating"] for r in judge) > 0:
        problems.append("the LLM-judge baseline made no judge calls")
    summary = f"{model}: {len(rows)} episodes, {calls} LLM steps, {errs} malformed, " \
              f"{sum(r['n_mutating'] for r in rows)} guarded calls, {sum(r['unsafe'] for r in rows)} unsafe"  # fmt: skip
    log(summary)
    return not problems, problems


def sample_replies(model: str, exp: str = "colab_smoke", root: Path | None = None, n: int = 2) -> list[str]:
    """The first reply without a tool call from up to ``n`` episodes: shows *what* the model wrote when a smoke test fails."""
    d = (root or ROOT) / "results" / "runs" / exp / model.replace(":", "-").replace("/", "-")
    out: list[str] = []
    for p in sorted(d.rglob("*.jsonl")) if d.exists() else []:
        for line in p.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("type") == "llm" and not rec.get("tool_calls"):
                out.append((rec.get("content") or "(empty reply)").strip()[:400])
                break
        if len(out) >= n:
            break
    return out


def _first_line(exc: BaseException) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]


def smoke_run(m: ModelInfo, backend: str, limit: int = 3, configs: str = SMOKE_CONFIGS) -> tuple[bool, list[str]]:
    """Smoke episodes for the model being served right now, then the acceptance checks (prints sample replies on failure)."""
    sname = served_name(m, backend)
    sg("run", "smoke", "--split", "dev", "--categories", "trap", "--limit", str(limit),
       "--models", sname, "--configs", configs, "--runs", "1", "--name", "colab_smoke", check=False)  # fmt: skip
    ok, problems = check_smoke(sname, min_episodes=limit * len(configs.split(";")))
    if not ok:
        for i, text in enumerate(sample_replies(sname), 1):
            log(f"sample reply without a tool call ({i}):\n" + "\n".join("      | " + x for x in text.splitlines()))
    return ok, problems


def smoke(models: list[str], backend: str, concurrency: int, limit: int = 3, configs: str = SMOKE_CONFIGS,
          command: list[str] | None = None, ready_url: str | None = None) -> bool:  # fmt: skip
    """Short end-to-end check of every model.  A failing model is reported and the next one is still tested."""
    verdicts: dict[str, str] = {}
    blocked = {} if command else preflight(models, backend)
    for name, why in blocked.items():
        log(f"SMOKE FAIL for {name}: {why}")
        verdicts[name] = "FAIL  no Hugging Face access (see above)"
    for name in models:
        if name in blocked:
            continue
        m = MODELS[name]
        t0 = time.time()
        try:
            with serve(m, backend, concurrency, command, ready_url):
                ok, problems = smoke_run(m, backend, limit, configs)
        except SystemExit as exc:  # the server would not start or the run crashed: report it, test the next model
            log(f"SMOKE FAIL for {name} after {time.time() - t0:.0f}s: {exc}")
            verdicts[name] = f"FAIL  {_first_line(exc)}"
            continue
        log(f"SMOKE {'PASS' if ok else 'FAIL'} for {name} in {time.time() - t0:.0f}s" + "".join(f"\n   - {p}" for p in problems))
        verdicts[name] = "PASS" if ok else f"FAIL  {problems[0]}"
    backup()  # keep the smoke results with the study, so a later session knows which models passed
    log("smoke summary:" + "".join(f"\n   {n:<12} {verdicts[n]}" for n in models))
    passed = [n for n in models if verdicts[n] == "PASS"]
    if passed and len(passed) < len(models):
        log(f"to run only the models that passed, set MODELS = \"{','.join(passed)}\" (cell 2) before cell 5; "
            "models added later only run their own episodes")  # fmt: skip
    return len(passed) == len(models)


def run_all(models: list[str], backend: str, concurrency: int, main_runs: int, skip: set[str], limit: int | None = None,
            command: list[str] | None = None, ready_url: str | None = None, smoke_first: bool = False) -> None:
    """Primary model: dev, main, ablations.  Other models: main.  One model load each.

    A model whose server fails is reported and skipped; the others still run, then the report is built.
    ``smoke_first`` smoke-tests each model in the same server session before its runs (one download and
    one load per model) and skips the model's runs if the smoke test fails.
    ``limit`` (first N tasks per split) is for rehearsals; ``command``/``ready_url`` swap in a test server.
    """
    blocked = {} if command else preflight(models, backend)
    if blocked:  # fail in seconds, before anything starts, rather than hours later
        raise SystemExit("cannot download these models:" + "".join(f"\n - {n}: {why}" for n, why in blocked.items())
                         + f"\nFix the access, or leave them out of MODELS for now (e.g. MODELS = "
                         f"\"{','.join(n for n in models if n not in blocked)}\"); they can be added later.")  # fmt: skip
    restore()
    # episodes judged on raw argument types before the fix in DECISIONS.md C12 are deleted here and re-run below
    sg("audit-args", "--delete", "--mirror", str(DRIVE_STATE / "results"), check=False)
    lim = ["--limit", str(limit)] if limit else []
    if "scripted" not in skip:  # LLM-free policy-level evaluation used by the report (CPU only, about a minute)
        sg("run", "scripted", *lim, check=False)
    order = sorted(models, key=lambda n: n != PRIMARY)
    failed: dict[str, str] = {}
    for name in order:
        m = MODELS[name]
        sname = served_name(m, backend)
        if not smoke_first and not any((d / "results" / "runs" / "colab_smoke" / sname).exists() for d in (ROOT, DRIVE_STATE)):
            log(f"note: no smoke-test results for {name} here or on Drive (fine if it passed in an earlier session)")
        t0 = time.time()
        try:
            with serve(m, backend, concurrency, command, ready_url), PeriodicBackup():
                if smoke_first:
                    ok, problems = smoke_run(m, backend)
                    log(f"SMOKE {'PASS' if ok else 'FAIL'} for {name}" + "".join(f"\n   - {x}" for x in problems))
                    if not ok:
                        raise SystemExit(f"smoke test failed ({problems[0]}); its runs were skipped")
                if name == PRIMARY and "dev" not in skip:
                    sg("run", "dev", "--models", sname, *lim, check=False)
                if "main" not in skip:
                    sg("run", "main", "--models", sname, "--runs", str(main_runs), *lim, check=False)
                if name == PRIMARY and "ablations" not in skip:
                    sg("run", "ablations", "--models", sname, *lim, check=False)
        except SystemExit as exc:  # keep going: the other models' runs are independent
            log(f"FAILED {name} after {(time.time() - t0) / 60:.0f} min: {exc}")
            failed[name] = _first_line(exc)
            continue
        log(f"finished {name} in {(time.time() - t0) / 60:.0f} min")
    report()
    if failed:
        raise SystemExit("these models did not run:" + "".join(f"\n - {n}: {why}" for n, why in failed.items())
                         + "\nThe report covers the others. After fixing the cause, run cell 5 again: finished "
                         "episodes are skipped.")  # fmt: skip


def run_posthoc(models: list[str], backend: str, concurrency: int, limit: int | None = None) -> None:
    """Post-hoc variant P1 (DECISIONS.md section 11): the main study's models under B4 + P1, live.

    Reported separately from B4 (`sg posthoc report`); the published B4 results are not touched.
    """
    restore()
    lim = ["--limit", str(limit)] if limit else []
    failed: dict[str, str] = {}
    for name in sorted(models, key=lambda n: n != PRIMARY):
        m = MODELS[name]
        try:
            with serve(m, backend, concurrency), PeriodicBackup():
                sg("run", "posthoc", "--models", served_name(m, backend), *lim, check=False)
        except SystemExit as exc:
            failed[name] = _first_line(exc)
    sg("physical", "posthoc", "--workers", "8", check=False)  # real-file measurement of the live P1 episodes (CPU)
    sg("posthoc", "report", check=False)
    report()
    if failed:
        raise SystemExit("these models did not run:" + "".join(f"\n - {n}: {why}" for n, why in failed.items()))


def report() -> None:
    sg("report")
    backup(include_cache=False)


def pack(dest: Path | None = None) -> Path:
    dest = dest or (DRIVE_STATE.parent if DRIVE_STATE.parent.exists() else ROOT / "dist")
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"storageguard_results_{time.strftime('%Y%m%d_%H%M')}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for d in ("results", "paper", "data/bench", "configs"):
            for p in (ROOT / d).rglob("*"):
                if p.is_file() and ".tmp" not in p.suffix:
                    z.write(p, p.relative_to(ROOT))
    log(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["setup", "access", "smoke", "all", "posthoc", "report", "pack", "restore", "backup"])
    ap.add_argument("--backend", choices=["vllm", "ollama"], default="vllm")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS), help=f"comma-separated, from {list(MODELS)}")
    ap.add_argument("--concurrency", type=int, default=None, help="parallel episodes (default 16 vLLM, 8 Ollama)")
    ap.add_argument("--main-runs", type=int, default=3)
    ap.add_argument("--skip", default="", help="comma-separated stages to skip in `all`: scripted,dev,main,ablations")
    ap.add_argument("--vllm-version", default="", help=f"vLLM version (default {DEFAULT_VLLM}; 'latest' = unpinned)")
    ap.add_argument("--smoke-limit", type=int, default=3)
    ap.add_argument("--smoke-configs", default=SMOKE_CONFIGS)
    ap.add_argument("--limit", type=int, default=None, help="rehearsal only: first N tasks of each split")
    ap.add_argument("--smoke-first", action="store_true", help="`all`: smoke-test each model just before its runs")
    a = ap.parse_args(argv)
    models = [x.strip() for x in a.models.split(",") if x.strip()]
    unknown = [x for x in models if x not in MODELS]
    if unknown:
        raise SystemExit(f"unknown models {unknown}; choose from {list(MODELS)}")
    conc = a.concurrency or (16 if a.backend == "vllm" else 8)
    if a.stage == "setup":
        setup(a.backend, a.vllm_version)
    elif a.stage == "access":
        if not access(models, a.backend):
            raise SystemExit("not every model can be downloaded yet: see above")
    elif a.stage == "smoke":
        if not smoke(models, a.backend, conc, a.smoke_limit, a.smoke_configs):
            raise SystemExit("smoke test FAILED: fix the problems above before starting the full run")
        log("smoke test PASSED for all models")
    elif a.stage == "all":
        run_all(models, a.backend, conc, a.main_runs, {s for s in a.skip.split(",") if s}, a.limit, smoke_first=a.smoke_first)
    elif a.stage == "posthoc":
        run_posthoc(models, a.backend, conc, a.limit)
    elif a.stage == "report":
        report()
    elif a.stage == "pack":
        pack()
    elif a.stage == "restore":
        restore()
    elif a.stage == "backup":
        backup(include_cache=True)


if __name__ == "__main__":
    main()
