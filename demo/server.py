"""Presentation server: the live API plus the built frontend, on one port.

    venv/bin/python demo/server.py            # http://127.0.0.1:8765
    venv/bin/python demo/server.py --port 9000

Standard library only (no extra packages in the research venv). Binds to localhost.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402

DIST = HERE / "web" / "dist"
STARTED = time.time()


def _q(qs: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    v = qs.get(key)
    return v[0] if v else default


class Handler(BaseHTTPRequestHandler):
    server_version = "StorageGuardDemo/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("  %s\n" % (fmt % args))

    # ---- helpers ----
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ctype.startswith("application/json") else "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj, default=str).encode(), "application/json; charset=utf-8")

    def _error(self, code: int, msg: str) -> None:
        self._json({"error": msg}, code)

    def _body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode() or "{}")

    # ---- routing ----
    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path.startswith("/api/"):
            return self._api("GET", u.path, parse_qs(u.query), {})
        return self._static(u.path)

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._error(404, "not found")
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._error(400, "request body is not valid JSON")
        return self._api("POST", u.path, parse_qs(u.query), body)

    def _api(self, method: str, path: str, qs: dict[str, list[str]], body: dict[str, Any]) -> None:
        try:
            if path == "/api/health":
                return self._json({"ok": True, "uptime_s": round(time.time() - STARTED), "tasks": len(engine.tasks())})
            if path == "/api/tasks":
                return self._json(engine.all_task_summaries())
            if path == "/api/map":
                return self._json(engine.task_map(_q(qs, "task") or ""))
            if path == "/api/recorded/index":
                return self._json(engine.recorded_index(_q(qs, "task") or ""))
            if path == "/api/recorded":
                return self._json(engine.replay_recorded(
                    _q(qs, "exp", "main"), _q(qs, "model") or "", _q(qs, "config") or "",
                    _q(qs, "task") or "", int(_q(qs, "run", "0") or 0)))  # fmt: skip
            if path == "/api/run" and method == "POST":
                return self._json(engine.run_live(body.get("task", ""), body.get("agent", "scripted:naive"),
                                                  body.get("config", "storageguard"), body.get("knobs") or {},
                                                  bool(body.get("physical"))))  # fmt: skip
            if path == "/api/sweep" and method == "POST":
                return self._json(engine.sweep(body.get("config", "storageguard"), body.get("knobs") or {}))
            return self._error(404, f"no endpoint {method} {path}")
        except KeyError as exc:
            return self._error(404, str(exc).strip("'\""))
        except ValueError as exc:
            return self._error(400, str(exc))
        except Exception as exc:  # noqa: BLE001 - report, keep serving
            traceback.print_exc()
            return self._error(500, f"{type(exc).__name__}: {exc}")

    def _static(self, path: str) -> None:
        if not DIST.exists():
            return self._send(200, b"<h1>Frontend not built</h1><p>Run <code>npm run build</code> in demo/web, "
                                   b"or use <code>npm run dev</code> for development.</p>", "text/html")  # fmt: skip
        rel = path.lstrip("/") or "index.html"
        target = (DIST / rel).resolve()
        if not str(target).startswith(str(DIST.resolve())) or not target.is_file():
            target = DIST / "index.html"  # single-page app: every unknown path is the app
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    engine.tasks()  # load the benchmark once, before the first request
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"StorageGuard presentation on http://{a.host}:{a.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
