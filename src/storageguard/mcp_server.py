"""Optional MCP server: the twin's tools behind StorageGuard, for live demos.

Run with ``python -m storageguard.mcp_server`` (needs ``pip install "storageguard[mcp]"``).
Every mutating tool call goes through the guard; escalations are *not*
auto-approved (there is no approver), so the client sees ``pending_approval``.
Environment: ``SG_MCP_SEED`` picks the generated scenario (default 7),
``SG_MCP_CONFIG`` the middleware config (default ``storageguard``).
"""

from __future__ import annotations

import inspect
import os
import typing
from typing import Any

from .agent.tools import parse_docstring, render
from .bench.scenario import generate_scenario
from .eval.baselines import make_middleware
from .guard.analyzers import Action
from .twin.operations import OPS, OpSpec
from .twin.state import StorageTwin


def build_server(twin: StorageTwin | None = None, config: str | None = None) -> Any:
    """Create a FastMCP server exposing every twin operation as an MCP tool."""
    try:
        try:
            from mcp.server.fastmcp import FastMCP  # mcp 1.x
        except ImportError:
            from mcp.server.mcpserver import MCPServer as FastMCP  # mcp 2.x renamed FastMCP to MCPServer
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise SystemExit('The MCP server needs the optional dependency: pip install "storageguard[mcp]"') from exc

    twin = twin or generate_scenario(int(os.environ.get("SG_MCP_SEED", "7"))).twin
    middleware = make_middleware(config or os.environ.get("SG_MCP_CONFIG", "storageguard"))
    server = FastMCP("storageguard-twin")

    def register(spec: OpSpec) -> None:
        sig = inspect.signature(spec.fn)
        hints = typing.get_type_hints(spec.fn)  # operations use postponed annotations: resolve them
        params = [p.replace(annotation=hints[p.name]) for p in list(sig.parameters.values())[1:]]  # drop `twin`

        def tool(**kwargs: Any) -> str:
            args = twin.resolve_args(spec.name, {k: v for k, v in kwargs.items() if v is not None})
            if not spec.mutating:
                return render(twin.execute(spec.name, args))
            payload, _ = middleware.handle(twin, Action(spec.name, args))
            return render(payload)

        tool.__name__ = spec.name
        tool.__doc__ = parse_docstring(spec.fn.__doc__ or "")[0]
        tool.__signature__ = sig.replace(parameters=params, return_annotation=str)  # type: ignore[attr-defined]
        tool.__annotations__ = {p.name: p.annotation for p in params} | {"return": str}
        server.add_tool(tool, name=spec.name, description=tool.__doc__)

    for spec in OPS.values():
        register(spec)
    return server


def main() -> None:  # pragma: no cover
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
