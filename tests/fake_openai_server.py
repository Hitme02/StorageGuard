"""A tiny OpenAI-compatible chat server for testing the Colab driver without a GPU.

Agent requests (with tools): list_pools -> create_snapshot(vol-1) -> delete_volume(vol-1, force) -> finish.
Judge requests (no tools): ALLOW.  Usage: python fake_openai_server.py PORT
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT = [("list_pools", {}), ("create_snapshot", {"volume_id": "vol-1"}),
          ("delete_volume", {"volume_id": "vol-1", "force": True}), ("finish", {"summary": "done"})]  # fmt: skip


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._send({"object": "list", "data": [{"id": "fake", "object": "model"}]})

    def do_POST(self):  # noqa: N802
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
        if not req.get("tools"):
            msg = {"role": "assistant", "content": '{"verdict": "ALLOW", "reason": "fake judge"}'}
            finish = "stop"
        else:
            # ids must be the driver's deterministic 9-character ids
            for m in req["messages"]:
                for tc in m.get("tool_calls") or []:
                    assert len(tc["id"]) == 9 and tc["id"].isalnum(), tc["id"]
            k = min(sum(1 for m in req["messages"] if m["role"] == "assistant"), len(SCRIPT) - 1)
            name, args = SCRIPT[k]
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"chatcmpl-tool-{k}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
            finish = "tool_calls"
        self._send({"id": "x", "object": "chat.completion", "created": 0, "model": req["model"],
                    "choices": [{"index": 0, "message": msg, "finish_reason": finish}], "usage": usage})  # fmt: skip


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
