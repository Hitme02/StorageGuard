"""The Colab driver: commands, model registry, Drive mirroring, and an end-to-end run against a fake server."""

import importlib.util
import json
import os
import re
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sg_colab", ROOT / "colab" / "sg_colab.py")
sgc = importlib.util.module_from_spec(spec)
sys.modules["sg_colab"] = sgc  # dataclasses need the module registered
spec.loader.exec_module(sgc)


def test_vllm_commands():
    q = sgc.vllm_command(sgc.MODELS["qwen2.5-7b"])
    assert q[1:3] == ["serve", "Qwen/Qwen2.5-7B-Instruct"]
    s = " ".join(q)
    assert "--enable-auto-tool-choice --tool-call-parser hermes" in s and "--generation-config vllm" in s
    assert "--chat-template" not in s and "--tokenizer-mode" not in s
    llama = " ".join(sgc.vllm_command(sgc.MODELS["llama3.1-8b"]))
    assert "--tool-call-parser llama3_json" in llama and llama.endswith("tool_chat_template_llama3.1_json.jinja")
    mistral = " ".join(sgc.vllm_command(sgc.MODELS["mistral-7b"]))
    assert "--tool-call-parser mistral" in mistral
    assert "--tokenizer-mode mistral --config-format mistral --load-format mistral" in mistral
    assert "--quantization awq_marlin" in " ".join(sgc.vllm_command(sgc.MODELS["qwen2.5-32b-awq"]))


def test_models_file_is_read_by_the_harness(tmp_path, monkeypatch):
    from storageguard.agent.llm_client import load_models

    p = sgc.write_models_file(sgc.MODELS["llama3.1-8b"], "vllm", 16, tmp_path / "m.yaml")
    monkeypatch.setenv("SG_MODELS_FILE", str(p))
    spec_ = load_models()["llama3.1-8b"]
    assert spec_.model == "meta-llama/Llama-3.1-8B-Instruct" and spec_.concurrency == 16
    assert spec_.base_url == "http://127.0.0.1:8000/v1"
    assert spec_.single_tool_call_turns  # Llama 3.1's template takes one tool call per message (C15)
    p = sgc.write_models_file(sgc.MODELS["qwen2.5-7b"], "ollama", 8, tmp_path / "o.yaml")
    spec_ = load_models(p)["qwen2.5-7b-q4"]  # the fallback backend never shares a results name with vLLM
    assert spec_.model == "sg-qwen2.5:7b" and spec_.base_url.endswith(":11434/v1")
    assert not spec_.single_tool_call_turns


def test_only_llama_needs_single_tool_call_turns():
    assert [n for n, m in sgc.MODELS.items() if m.single_tool_calls] == ["llama3.1-8b"]


def test_chat_template_is_shipped_and_pinned(tmp_path, monkeypatch):
    shipped = sgc.ROOT / "colab" / "templates"
    name = sgc.MODELS["llama3.1-8b"].template
    monkeypatch.setattr(sgc.urllib.request, "urlretrieve", lambda *a, **k: pytest.fail("the template must not be downloaded"))
    assert sgc.fetch_template(name, shipped) == shipped / name  # the copy in the repo passes the hash check
    assert f"v{sgc.DEFAULT_VLLM}/" in sgc.TEMPLATE_URL  # fallback download is pinned to the vLLM release, not main
    (tmp_path / name).write_text((shipped / name).read_text() + "\n")
    with pytest.raises(SystemExit, match="does not match"):
        sgc.fetch_template(name, tmp_path)


def test_sync_tree_copies_only_changes(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    (src / "x").mkdir(parents=True)
    (src / "x" / "1.json").write_text("one")
    (src / "2.tmp").write_text("partial")
    assert sgc.sync_tree(src, dst) == 1 and (dst / "x" / "1.json").read_text() == "one"
    assert not (dst / "2.tmp").exists()
    assert sgc.sync_tree(src, dst) == 0
    (src / "x" / "1.json").write_text("one, longer")
    assert sgc.sync_tree(src, dst) == 1 and (dst / "x" / "1.json").read_text() == "one, longer"
    assert sgc.sync_tree(tmp_path / "missing", dst) == 0


def test_check_smoke_flags_problems(tmp_path):
    d = tmp_path / "results" / "runs" / "colab_smoke" / "m" / "noguard"
    d.mkdir(parents=True)
    base = {"termination": "finish", "n_mutating": 1, "finished": 1, "steps": 4, "errors": 0, "unsafe": 0,
            "config": "noguard", "extra_llm_calls": 0}  # fmt: skip
    (d / "t1.json").write_text(json.dumps(base))
    assert sgc.check_smoke("m", root=tmp_path) == (True, [])
    (d / "t1.json").write_text(json.dumps(base | {"termination": "no_tool_call", "n_mutating": 0, "finished": 0, "errors": 4}))
    ok, problems = sgc.check_smoke("m", root=tmp_path, min_episodes=2)
    assert not ok and len(problems) == 5


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not (ROOT / "data" / "bench" / "tasks.json").exists(), reason="benchmark not generated")
def test_end_to_end_smoke_against_fake_vllm(tmp_path, monkeypatch):
    """Driver -> server process -> `sg run` (subprocess, 4 configs, concurrent) -> smoke checks."""
    port = _free_port()
    work = tmp_path / "root"
    (work / "data").mkdir(parents=True)
    os.symlink(ROOT / "data" / "bench", work / "data" / "bench")
    os.symlink(ROOT / "configs", work / "configs_src")
    (work / "configs").mkdir()
    for p in (ROOT / "configs").iterdir():
        if p.name != "models_colab.yaml":
            os.symlink(p, work / "configs" / p.name)
    (work / "pyproject.toml").write_text("")
    monkeypatch.setenv("SG_ROOT", str(work))
    monkeypatch.setattr(sgc, "ROOT", work)
    monkeypatch.setattr(sgc, "MODELS_FILE", work / "configs" / "models_colab.yaml")
    monkeypatch.setattr(sgc, "VLLM_PORT", port)
    monkeypatch.setattr(sgc, "SG_VENV", Path(sys.executable).parent.parent)
    monkeypatch.setattr(sgc, "DRIVE_STATE", tmp_path / "no-drive" / "state")
    cmd = [sys.executable, str(ROOT / "tests" / "fake_openai_server.py"), str(port)]
    m = sgc.MODELS["qwen2.5-7b"]
    with sgc.serve(m, "vllm", 4, command=cmd, ready_url=f"http://127.0.0.1:{port}/v1/models"):
        sgc.sg("run", "smoke", "--split", "dev", "--categories", "trap", "--limit", "2", "--models", "qwen2.5-7b",
               "--configs", sgc.SMOKE_CONFIGS, "--runs", "1", "--name", "colab_smoke")  # fmt: skip
    ok, problems = sgc.check_smoke("qwen2.5-7b", min_episodes=8, root=work)
    assert ok, problems
    rows = [json.loads(p.read_text()) for p in (work / "results" / "runs" / "colab_smoke").rglob("*.json")]
    by_cfg = {r["config"]: r for r in rows}
    assert by_cfg["llm_judge"]["extra_llm_calls"] >= 1
    assert by_cfg["rbac"]["n_escalations"] >= 1  # the forced delete needs approval
    assert all(r["finished"] and r["steps"] == 4 for r in rows)
    out = sgc.pack(tmp_path / "dist")
    assert out.exists() and out.stat().st_size > 0


@pytest.mark.skipif(not (ROOT / "data" / "bench" / "tasks.json").exists(), reason="benchmark not generated")
def test_full_pipeline_rehearsal_against_fake_vllm(tmp_path, monkeypatch):
    """`all` with --limit 1: dev, main, ablations for the primary model, main for a second model, report, Drive backup."""
    port = _free_port()
    work = tmp_path / "root"
    (work / "data").mkdir(parents=True)
    os.symlink(ROOT / "data" / "bench", work / "data" / "bench")
    (work / "configs").mkdir()
    for p in (ROOT / "configs").iterdir():
        if p.name != "models_colab.yaml":
            os.symlink(p, work / "configs" / p.name)
    (work / "pyproject.toml").write_text("")
    drive = tmp_path / "drive" / "storageguard" / "state"
    drive.parent.mkdir(parents=True)
    monkeypatch.setenv("SG_ROOT", str(work))
    monkeypatch.setattr(sgc, "ROOT", work)
    monkeypatch.setattr(sgc, "MODELS_FILE", work / "configs" / "models_colab.yaml")
    monkeypatch.setattr(sgc, "VLLM_PORT", port)
    monkeypatch.setattr(sgc, "SG_VENV", Path(sys.executable).parent.parent)
    monkeypatch.setattr(sgc, "DRIVE_STATE", drive)
    cmd = [sys.executable, str(ROOT / "tests" / "fake_openai_server.py"), str(port)]
    sgc.run_all(["llama3.1-8b", "qwen2.5-7b"], "vllm", 4, main_runs=1, skip=set(), limit=1,
                command=cmd, ready_url=f"http://127.0.0.1:{port}/v1/models")  # fmt: skip
    runs = work / "results" / "runs"
    n = {exp: len(list((runs / exp).rglob("*.json"))) for exp in ("scripted", "dev", "main", "ablations")}
    # scripted: 2 plans x 6 configs; dev: 9 configs; main: 5 configs x 2 models; ablations: 12 configs
    assert n == {"scripted": 12, "dev": 9, "main": 10, "ablations": 12}, n
    assert (work / "results" / "REPORT.md").exists() and (work / "results" / "episodes.csv").exists()
    assert len(list((work / "paper" / "figures").glob("*.pdf"))) == 6
    assert len(list((drive / "results" / "runs").rglob("*.json"))) == 43  # mirrored to "Drive"
    assert (drive / ".cache").exists()


def test_hf_access_explains_each_failure_and_never_forwards_the_token():
    import http.server
    import threading

    seen: dict[str, str | None] = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):  # noqa: N802
            seen[self.path] = self.headers.get("Authorization")
            code = {"/ok": 200, "/redir": 302, "/no": 403, "/auth": 401, "/gone": 404, "/odd": 500}.get(self.path, 200)
            self.send_response(code)
            if code == 302:
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/leak")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    m = sgc.MODELS["llama3.1-8b"]
    try:
        assert sgc.hf_access(m, token="hf_x", url=f"{base}/ok") is None
        assert seen["/ok"] == "Bearer hf_x"
        assert sgc.hf_access(m, token="hf_x", url=f"{base}/redir") is None
        assert "/leak" not in seen  # the redirect is not followed, so the token stays with Hugging Face
        assert "no access to meta-llama/Llama-3.1-8B-Instruct" in sgc.hf_access(m, token="hf_x", url=f"{base}/no")
        assert "rejected" in sgc.hf_access(m, token="hf_x", url=f"{base}/auth")
        assert "no HF_TOKEN" in sgc.hf_access(m, token="", url=f"{base}/auth")
        assert seen["/auth"] is None  # no token, no header
        assert "not found" in sgc.hf_access(m, token="hf_x", url=f"{base}/gone")
        assert sgc.hf_access(m, token="hf_x", url=f"{base}/odd") is None  # unknown answers do not block a run
    finally:
        srv.shutdown()
    assert sgc.hf_access(m, token="hf_x", url="http://127.0.0.1:9/x", timeout=2) is None  # offline: try anyway
    assert sgc.preflight(["llama3.1-8b"], "ollama") == {}  # Ollama does not download from Hugging Face


def _fake_stages(monkeypatch, tmp_path, fail: set[str]):
    import contextlib

    served: list[str] = []

    @contextlib.contextmanager
    def fake_serve(m, backend, concurrency, command=None, ready_url=None):
        if m.name in fail:
            raise SystemExit("model server exited with code 1. Last log lines:\nOSError: gated repo")
        served.append(m.name)
        yield

    calls: list[str] = []
    monkeypatch.setattr(sgc, "ROOT", tmp_path)
    monkeypatch.setattr(sgc, "DRIVE_STATE", tmp_path / "no-drive" / "state")
    monkeypatch.setattr(sgc, "serve", fake_serve)
    monkeypatch.setattr(sgc, "sg", lambda *a, **k: calls.append(" ".join(a)) or 0)
    monkeypatch.setattr(sgc, "check_smoke", lambda model, **k: (True, []))
    monkeypatch.setattr(sgc, "report", lambda: calls.append("report"))
    monkeypatch.setattr(sgc, "restore", lambda: calls.append("restore"))
    return served, calls


def test_smoke_reports_every_model_even_when_one_fails(tmp_path, monkeypatch, capsys):
    served, _ = _fake_stages(monkeypatch, tmp_path, fail={"llama3.1-8b"})
    monkeypatch.setattr(sgc, "hf_access", lambda m, **k: "no access" if m.name == "qwen2.5-32b-awq" else None)
    ok = sgc.smoke(["qwen2.5-7b", "llama3.1-8b", "mistral-7b", "qwen2.5-32b-awq"], "vllm", 4)
    out = capsys.readouterr().out
    assert not ok and served == ["qwen2.5-7b", "mistral-7b"]  # llama failed, 32b never started, mistral still tested
    assert "llama3.1-8b  FAIL  model server exited with code 1" in out and "qwen2.5-32b-awq FAIL  no Hugging Face access" in out
    assert 'MODELS = "qwen2.5-7b,mistral-7b"' in out


def test_run_all_stops_before_starting_when_a_model_is_not_downloadable(tmp_path, monkeypatch):
    served, calls = _fake_stages(monkeypatch, tmp_path, fail=set())
    monkeypatch.setattr(sgc, "hf_access", lambda m, **k: "no access yet" if m.name == "llama3.1-8b" else None)
    with pytest.raises(SystemExit, match="llama3.1-8b: no access yet") as exc:
        sgc.run_all(["qwen2.5-7b", "llama3.1-8b", "mistral-7b"], "vllm", 4, 1, set())
    assert 'MODELS = "qwen2.5-7b,mistral-7b"' in str(exc.value)
    assert served == [] and calls == []  # nothing ran, nothing restored


def test_run_all_keeps_going_past_a_failing_model_and_still_reports(tmp_path, monkeypatch):
    served, calls = _fake_stages(monkeypatch, tmp_path, fail={"llama3.1-8b"})
    monkeypatch.setattr(sgc, "hf_access", lambda m, **k: None)
    with pytest.raises(SystemExit, match="llama3.1-8b: model server exited") as exc:
        sgc.run_all(["llama3.1-8b", "qwen2.5-7b", "mistral-7b"], "vllm", 4, 1, set())
    assert "finished episodes are skipped" in str(exc.value)
    assert served == ["qwen2.5-7b", "mistral-7b"]  # primary first, then the rest; llama skipped
    assert calls[0] == "restore" and calls[-1] == "report"
    assert "run main --models mistral-7b --runs 1" in calls and "run ablations --models qwen2.5-7b" in calls


def test_posthoc_stage_runs_p1_per_model_then_measures_and_reports(tmp_path, monkeypatch):
    served, calls = _fake_stages(monkeypatch, tmp_path, fail=set())
    monkeypatch.setattr(sgc, "PeriodicBackup", lambda: __import__("contextlib").nullcontext())
    sgc.run_posthoc(["llama3.1-8b", "qwen2.5-7b"], "vllm", 4)
    assert served == ["qwen2.5-7b", "llama3.1-8b"]  # primary first
    assert calls[0] == "restore" and calls[-1] == "report"
    assert sum(c.startswith("run posthoc --models") for c in calls) == 2
    assert calls.index("physical posthoc --workers 8") < calls.index("posthoc report")


def _notebooks():
    spec_ = importlib.util.spec_from_file_location("make_colab_notebook", ROOT / "scripts" / "make_colab_notebook.py")
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)
    return {name: mod.build(cells) for name, cells in mod.NOTEBOOKS.items()}


def test_notebooks_parse_and_only_call_existing_stages():
    import ast
    import re

    stages = {"setup", "access", "smoke", "all", "posthoc", "report", "pack", "restore", "backup"}
    for name, nb in _notebooks().items():
        code = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
        for src in code:
            ast.parse("\n".join(line for line in src.splitlines() if not line.lstrip().startswith("!")))
        called = set(re.findall(r'run\("(\w+)"', "\n".join(code)))
        assert called and called <= stages, (name, called - stages)
    cont = "\n".join("".join(c["source"]) for c in _notebooks()["StorageGuard_Colab_Continue.ipynb"]["cells"])
    assert 'MODELS    = "qwen2.5-7b,ministral-3-8b"' in cont and 'run("access", "--models", LATER)' in cont
    assert 'run("all", "--backend", BACKEND, "--models", SCALE, "--main-runs", SCALE_RUNS, "--smoke-first")' in cont
    scale = "\n".join("".join(c["source"]) for c in _notebooks()["StorageGuard_Colab_Scale.ipynb"]["cells"])
    assert 'run("all", "--backend", BACKEND, "--models", SCALE, "--main-runs", SCALE_RUNS, "--smoke-first")' in scale


def test_continue_notebook_settings_cell_runs(tmp_path, monkeypatch):
    """Cell 2 with google.colab stubbed: newest zip wins, code is unpacked, helpers work end to end."""
    import time as _time
    import types
    import zipfile

    drive = tmp_path / "MyDrive" / "storageguard"
    drive.mkdir(parents=True)
    for i, name in enumerate(("storageguard_colab.zip", "storageguard_colab (1).zip")):
        with zipfile.ZipFile(drive / name, "w") as z:
            z.write(ROOT / "colab" / "sg_colab.py", "storageguard/colab/sg_colab.py")
            z.writestr("storageguard/pyproject.toml", f"# bundle {i}\n")
        os.utime(drive / name, (_time.time() - 100 + i * 50,) * 2)  # "(1)" is the newer upload
    colab = types.ModuleType("google.colab")
    colab.drive = types.SimpleNamespace(mount=lambda path: None)
    colab.userdata = types.SimpleNamespace(get=lambda key: "hf_test")
    google = types.ModuleType("google")
    google.colab = colab
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.colab", colab)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SG_DRIVE_STATE", str(tmp_path / "unused"))
    nb = _notebooks()["StorageGuard_Colab_Continue.ipynb"]
    cell2 = next("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code" and "DRIVE_DIR =" in "".join(c["source"]))
    content = tmp_path / "content"
    src = cell2.replace('"/content/drive/MyDrive/storageguard"', f'"{drive}"').replace('"/content', f'"{content}')
    ns: dict = {}
    exec(compile(src, "cell2", "exec"), ns)  # noqa: S102
    assert Path.cwd() == content / "storageguard"
    assert (content / "storageguard" / "pyproject.toml").read_text() == "# bundle 1\n"  # the newest zip was used
    assert os.environ["HF_TOKEN"] == "hf_test" and ns["LATER"] == "llama3.1-8b"
    ns["run"]("access", "--backend", "ollama", "--models", "qwen2.5-7b")  # driver from the zip, through run()
    with pytest.raises(RuntimeError, match="step failed"):
        ns["run"]("access", "--models", "no-such-model")


def test_study_models_and_their_serving_settings():
    ministral = " ".join(sgc.vllm_command(sgc.MODELS["ministral-3-8b"]))
    assert "mistralai/Ministral-3-8B-Instruct-2512-BF16" in ministral and "--tool-call-parser mistral" in ministral
    assert "--tokenizer-mode mistral --config-format mistral --load-format mistral" in ministral
    q14 = " ".join(sgc.vllm_command(sgc.MODELS["qwen2.5-14b"]))
    assert "Qwen/Qwen2.5-14B-Instruct " in q14 and "--quantization" not in q14  # full precision on 80 GB
    q32 = " ".join(sgc.vllm_command(sgc.MODELS["qwen2.5-32b-int8"]))
    assert "GPTQ-Int8" in q32 and "--quantization" not in q32  # vLLM picks the GPTQ kernel itself
    assert "--quantization awq_marlin" in " ".join(sgc.vllm_command(sgc.MODELS["qwen2.5-72b-awq"]))
    assert "--quantization awq_marlin" in " ".join(sgc.vllm_command(sgc.MODELS["qwen2.5-7b-awq"]))
    assert "mistral-7b" not in sgc.DEFAULT_MODELS and "ministral-3-8b" in sgc.DEFAULT_MODELS
    assert set(sgc.SCALE_MODELS) | set(sgc.SCALE_MODELS_40GB) <= set(sgc.MODELS)


def test_models_file_caps_concurrency_for_large_models(tmp_path):
    from storageguard.agent.llm_client import load_models

    p = sgc.write_models_file(sgc.MODELS["qwen2.5-72b-awq"], "vllm", 16, tmp_path / "m.yaml")
    assert load_models(p)["qwen2.5-72b-awq"].concurrency == 8
    p = sgc.write_models_file(sgc.MODELS["qwen2.5-7b"], "vllm", 16, tmp_path / "m.yaml")
    assert load_models(p)["qwen2.5-7b"].concurrency == 16


def test_sample_replies_shows_what_the_model_wrote(tmp_path):
    d = tmp_path / "results" / "runs" / "colab_smoke" / "m" / "noguard"
    d.mkdir(parents=True)
    recs = [{"type": "message", "role": "system", "content": "sys"},
            {"type": "llm", "step": 1, "content": "get_volume arch-a8c4\nfinish done", "tool_calls": []},
            {"type": "llm", "step": 2, "content": "", "tool_calls": []}]  # fmt: skip
    (d / "t1__r0.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
    (d / "t2__r0.jsonl").write_text(json.dumps({"type": "llm", "content": "", "tool_calls": []}))
    assert sgc.sample_replies("m", root=tmp_path) == ["get_volume arch-a8c4\nfinish done", "(empty reply)"]
    assert sgc.sample_replies("missing", root=tmp_path) == []


def test_vllm_is_pinned_with_its_companion_packages(tmp_path, monkeypatch):
    assert sgc.vllm_requirements() == ["vllm==0.33.0", "mistral-common[image]==1.12.0"]
    assert sgc.vllm_requirements("0.31.0") == ["vllm==0.31.0"]
    assert sgc.vllm_requirements("latest") == ["vllm"]
    monkeypatch.setattr(sgc, "ROOT", tmp_path)
    monkeypatch.setattr(sgc, "SG_VENV", Path(sys.executable).parent.parent)
    monkeypatch.setattr(sgc, "VLLM_VENV", tmp_path / "no-vllm-venv")
    out = sgc.record_environment(uv=os.environ.get("UV", "uv"))
    harness = (out / "harness_packages.txt").read_text()
    assert "pydantic==" in harness and not (out / "vllm_packages.txt").exists()


def test_figures_render_under_a_notebook_backend_setting():
    """Colab exports MPLBACKEND=module://matplotlib_inline...; the harness must still draw its figures."""
    import subprocess

    env = {**os.environ, "MPLBACKEND": "module://matplotlib_inline.backend_inline"}
    r = subprocess.run([sys.executable, "-c", "import storageguard.eval.plots as p; import matplotlib; print(matplotlib.get_backend())"],
                       env=env, capture_output=True, text=True)  # fmt: skip
    assert r.returncode == 0, r.stderr[-500:]
    assert r.stdout.strip().lower() == "agg"


def test_preflight_rejects_models_too_large_for_the_gpu(monkeypatch):
    monkeypatch.setattr(sgc, "hf_access", lambda m, **k: None)
    on_40 = sgc.preflight(sgc.SCALE_MODELS, "vllm", gpu_gb=39.6)  # nvidia-smi: 40960 MiB
    assert set(on_40) == {"qwen2.5-14b", "qwen2.5-32b-int8", "qwen2.5-72b-awq"} and "40 GB ladder" in on_40["qwen2.5-72b-awq"]
    assert sgc.preflight(sgc.SCALE_MODELS, "vllm", gpu_gb=80.0) == {}  # nvidia-smi: 81920 MiB
    assert sgc.preflight(sgc.SCALE_MODELS_40GB + sgc.DEFAULT_MODELS, "vllm", gpu_gb=39.6) == {}


def test_ensure_disk_frees_space_from_other_models_only(tmp_path):
    hub = tmp_path / "hub"
    for name, gb in (("models--Qwen--Qwen2.5-7B-Instruct", 3), ("models--mistralai--X", 2), ("models--Qwen--Qwen2.5-72B-Instruct-AWQ", 1)):
        (hub / name / "blobs").mkdir(parents=True)
        (hub / name / "blobs" / "w").write_bytes(b"0" * (gb * 1000))
    (hub / "datasets--keep").mkdir()
    m = sgc.MODELS["qwen2.5-72b-awq"]
    # plenty of space: nothing is removed
    assert sgc.ensure_disk(m, hub=hub, free_gb=500) == []
    # short of space: the largest other model goes first, the model itself and non-model folders stay
    removed = sgc.ensure_disk(m, hub=hub, free_gb=41.6 + 15 - 0.0000025, margin_gb=15)
    assert removed == ["models--Qwen--Qwen2.5-7B-Instruct"]
    assert (hub / "models--Qwen--Qwen2.5-72B-Instruct-AWQ").exists() and (hub / "datasets--keep").exists()
    assert sgc.ensure_disk(m, hub=tmp_path / "missing", free_gb=0) == []


def test_run_all_smoke_first_skips_a_model_that_fails_its_smoke_test(tmp_path, monkeypatch):
    served, calls = _fake_stages(monkeypatch, tmp_path, fail=set())
    monkeypatch.setattr(sgc, "hf_access", lambda m, **k: None)
    monkeypatch.setattr(sgc, "gpu_memory_gb", lambda: 80.0)
    monkeypatch.setattr(sgc, "check_smoke", lambda model, **k: (model != "qwen2.5-32b-int8", ["never produced a valid tool call"]))
    with pytest.raises(SystemExit, match="qwen2.5-32b-int8: smoke test failed"):
        sgc.run_all(sgc.SCALE_MODELS, "vllm", 4, 1, set(), smoke_first=True)
    assert served == sgc.SCALE_MODELS  # one server session per model, smoke test inside it
    mains = [c for c in calls if c.startswith("run main")]
    assert mains == [f"run main --models {m} --runs 1" for m in sgc.SCALE_MODELS if m != "qwen2.5-32b-int8"]
    assert sum(c.startswith("run smoke") for c in calls) == 4 and calls[-1] == "report"


@pytest.mark.parametrize(("mib", "ladder"), [("81920", "LADDER_80GB"), ("40960", "LADDER_40GB"), (None, "LADDER_40GB")])
def test_scale_notebook_picks_the_ladder_for_the_gpu(tmp_path, monkeypatch, mib, ladder):
    nb = _notebooks()["StorageGuard_Colab_Scale.ipynb"]
    cell2 = next("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code" and "SCALE      =" in "".join(c["source"]))
    settings = cell2.split("import os, sys, time, zipfile")[0]  # the settings block, before the shared Drive/project code
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if mib is not None:
        (bin_dir / "nvidia-smi").write_text(f"#!/bin/sh\necho {mib}\n")
        (bin_dir / "nvidia-smi").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    ns: dict = {}
    exec(compile(settings, "cell2", "exec"), ns)  # noqa: S102
    assert ns["SCALE"] == ns[ladder]
    assert set(ns["SCALE"].split(",")) <= set(sgc.MODELS) and ns["SCALE_RUNS"] == 1


def test_llama_notebook_has_only_the_required_cells():
    cells = _notebooks()["StorageGuard_Colab_Llama.ipynb"]["cells"]
    code = ["".join(c["source"]) for c in cells if c["cell_type"] == "code"]
    assert len(code) == 5  # GPU, settings + Drive, setup, run, report + download
    assert 'run("setup"' in code[2] and re.search(r'^LLAMA\s*=\s*"llama3.1-8b"', code[1], re.M)
    assert '"--models", LLAMA' in code[3] and "SCALE" not in "".join(code)  # finished scale models are not touched
    assert "download_results()" in code[4]
