"""Generic A2A server: hosts ONE mesh node (any agent spec) on its own port.

Usage:
    python a2a_server.py --agent example_specialist
    python a2a_server.py --agent example_coordinator --port 8120

MCP-backed nodes (any spec with ``type: mcp`` tools) get every MCP session they
need opened and held for the node's lifetime, with exponential-backoff retries
at startup and a reconnect loop if a session drops mid-flight (reference pattern,
generalised from one MCP tool per node to several).
"""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONWARNINGS", "ignore")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import contextlib  # noqa: E402
import pathlib  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from src.agents.node_registry import NODE_MCP_SERVICES, NODE_NAMES, NODE_PORTS, SPECS, build_node  # noqa: E402
from src.a2a.hosting import build_agent_card, build_starlette_app, serve  # noqa: E402
from src.config import Config  # noqa: E402
from src.factory.model_clients import ModelConfigError, build_chat_client, resolve_model  # noqa: E402
from src.integrations.mcp_clients import make_mcp_tool  # noqa: E402
from src.observability import setup_observability  # noqa: E402

_MCP_RETRIES = 8
_MCP_BACKOFF_BASE = 1.0      # seconds; doubles per startup attempt
_MCP_RECONNECT_DELAY = 5.0   # after a mid-session drop


def _serve_mcp_node(name: str, port: int) -> None:
    import uvicorn

    spec = SPECS[name]

    async def _run() -> None:
        ever_started, attempt = False, 0
        while True:
            attempt += 1
            try:
                async with contextlib.AsyncExitStack() as stack:
                    tools = {}
                    for ref in spec.mcp_tools:
                        tool = make_mcp_tool(ref.service, ref.allow, owner=name)
                        tools[ref.service] = await stack.enter_async_context(tool)
                    agent, public_name, _ = build_node(name, mcp_tools=tools)
                    app = build_starlette_app(agent, build_agent_card(spec, port), node=name)
                    print(f"[mesh] '{name}' ({public_name}) on http://{Config.A2A_HOST}:{port}/ "
                          f"(MCP connected: {', '.join(tools)})", flush=True)
                    server = uvicorn.Server(uvicorn.Config(app, host=Config.A2A_HOST, port=port, log_level="warning"))
                    ever_started, attempt = True, 0
                    await server.serve()
                    return
            except Exception as exc:
                if ever_started:
                    print(f"[mesh] '{name}' MCP session dropped: {exc}. Reconnecting in {_MCP_RECONNECT_DELAY:.0f}s",
                          flush=True)
                    await asyncio.sleep(_MCP_RECONNECT_DELAY)
                elif attempt >= _MCP_RETRIES:
                    print(f"[mesh] '{name}' could not connect MCP services {NODE_MCP_SERVICES[name]} after "
                          f"{_MCP_RETRIES} attempts. Are they running? Last error: {exc}", flush=True)
                    raise SystemExit(1) from exc
                else:
                    delay = _MCP_BACKOFF_BASE * (2 ** (attempt - 1))
                    print(f"[mesh] '{name}' MCP connect failed ({attempt}/{_MCP_RETRIES}): {exc}. "
                          f"Retrying in {delay:.0f}s", flush=True)
                    await asyncio.sleep(delay)

    asyncio.run(_run())


def main() -> None:
    parser = argparse.ArgumentParser(description="Host one ISTR mesh node as an A2A server.")
    parser.add_argument("--agent", required=True, choices=NODE_NAMES)
    parser.add_argument("--port", type=int, default=None, help="Override the spec/env port.")
    args = parser.parse_args()

    setup_observability(service_name=f"istr_{args.agent}")
    spec = SPECS[args.agent]
    try:  # fail fast on provider/model/key problems, before binding a port
        build_chat_client(spec)
    except ModelConfigError as exc:
        print(f"[mesh] ERROR starting '{args.agent}': {exc}", flush=True)
        sys.exit(1)
    print(f"[mesh] '{args.agent}' LLM: {resolve_model(spec).describe()}", flush=True)

    port = args.port or NODE_PORTS[args.agent]
    if NODE_MCP_SERVICES[args.agent]:
        _serve_mcp_node(args.agent, port)
        return
    agent, public_name, _ = build_node(args.agent)
    print(f"[mesh] '{args.agent}' ({public_name}) on http://{Config.A2A_HOST}:{port}/", flush=True)
    serve(agent, build_agent_card(spec, port), port, node=args.agent)


if __name__ == "__main__":
    main()
