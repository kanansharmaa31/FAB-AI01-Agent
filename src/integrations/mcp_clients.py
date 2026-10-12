"""MCP client factories for the ISTR data services.

Returns *unconnected* ``MCPStreamableHTTPTool`` instances, as in the reference.
``a2a_server.py`` opens them (``async with``) and keeps the sessions alive for
the node's lifetime, with retry/backoff and mid-session reconnect.

Least privilege (ISTR rule 5) is enforced by MAF itself: ``allowed_tools`` makes
the framework expose only the spec's ``allow`` list to the model, even if the
service publishes more tools.

Each service sits behind its ``*_MCP_URL``; pointing that URL at the real
GERNAS SQL Agent / KG / RAG MCP endpoint is the only change needed to go live.
"""
from __future__ import annotations

from agent_framework import MCPStreamableHTTPTool

from src.config import Config
from src.integrations.mcp_catalog import SERVICE_CATALOG, SERVICE_DESCRIPTIONS


def make_mcp_tool(service: str, allow: list[str] | None = None, owner: str = "") -> MCPStreamableHTTPTool:
    """Builds an (unconnected) MCP client for ``service`` exposing only ``allow``."""
    if service not in SERVICE_CATALOG:
        raise ValueError(f"Unknown MCP service '{service}'. Known: {', '.join(sorted(SERVICE_CATALOG))}")
    kwargs: dict = dict(
        name=f"{service}_mcp" if not owner else f"{owner}__{service}_mcp",
        url=Config.mcp_url(service),
        description=SERVICE_DESCRIPTIONS.get(service, service),
        request_timeout=Config.MCP_REQUEST_TIMEOUT,
        allowed_tools=list(allow) if allow else list(SERVICE_CATALOG[service]),
        load_prompts=False,
    )
    if Config.MCP_API_KEY:
        kwargs["static_headers"] = {"X-API-Key": Config.MCP_API_KEY}
    return MCPStreamableHTTPTool(**kwargs)
