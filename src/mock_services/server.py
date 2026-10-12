"""Hosts the synthetic services as FastMCP streamable-HTTP servers.

Usage:
    python -m src.mock_services                 # all five services, one process
    python -m src.mock_services --services kyc,screening

Each service binds to the host/port in its ``*_MCP_URL``. A service whose URL
points at a non-local host is skipped: that means a real GERNAS service has been
plugged in and the mock must not shadow it.
"""
from __future__ import annotations

import argparse
import asyncio
from types import ModuleType
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

from src.config import Config
from src.mock_services import graph, kyc, policy_rag, screening, transactions

SERVICE_MODULES: dict[str, ModuleType] = {
    m.SERVICE: m for m in (transactions, kyc, screening, graph, policy_rag)
}

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def build_server(service: str) -> FastMCP:
    """Builds the FastMCP server for one service, registering its plain functions as tools."""
    module = SERVICE_MODULES[service]
    url = urlparse(Config.mcp_url(service))
    server = FastMCP(
        name=f"mock-{service}",
        instructions=f"SYNTHETIC mock of the '{service}' service for the ISTR mesh. Test data only.",
        host=url.hostname or "127.0.0.1",
        port=url.port or 80,
        streamable_http_path=url.path or "/mcp",
        stateless_http=True,
        log_level="WARNING",
    )
    for fn in module.TOOLS:
        server.tool()(fn)
    return server


def local_services(selected: list[str] | None = None) -> list[str]:
    """Services that should run as local mocks (their URL is on this machine)."""
    names = selected or list(SERVICE_MODULES)
    return [s for s in names if (urlparse(Config.mcp_url(s)).hostname or "") in _LOCAL_HOSTS]


async def serve(services: list[str]) -> None:
    servers = [build_server(s) for s in services]
    for s, srv in zip(services, servers):
        print(f"[mock] {s:<13} -> {Config.mcp_url(s)}  tools={[f.__name__ for f in SERVICE_MODULES[s].TOOLS]}")
    await asyncio.gather(*(srv.run_streamable_http_async() for srv in servers))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the synthetic ISTR MCP services.")
    parser.add_argument("--services", default="", help="Comma-separated subset (default: all local).")
    args = parser.parse_args()
    selected = [s.strip() for s in args.services.split(",") if s.strip()] or None
    unknown = set(selected or []) - set(SERVICE_MODULES)
    if unknown:
        parser.error(f"unknown services: {', '.join(sorted(unknown))}")
    services = local_services(selected)
    skipped = set(selected or SERVICE_MODULES) - set(services)
    for s in sorted(skipped):
        print(f"[mock] {s:<13} skipped: URL is remote ({Config.mcp_url(s)}), real service assumed")
    if not services:
        print("[mock] nothing to run")
        return
    try:
        asyncio.run(serve(services))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
