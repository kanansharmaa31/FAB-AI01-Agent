"""Hosts an Agent Framework agent as an A2A-protocol HTTP server (same pattern as the reference).

Starlette + a2a-sdk JSON-RPC routes + ``agent_framework.a2a.A2AExecutor``.
Adds ``GET /health`` for the launcher / api_server fan-out, and continues the
caller's distributed trace from inbound W3C headers.
"""
from __future__ import annotations

import time as _time

import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from agent_framework.a2a import A2AExecutor
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.config import Config
from src.factory.spec import AgentSpec


class TraceContextMiddleware(BaseHTTPMiddleware):
    """Attaches inbound traceparent + baggage so node spans join the caller's trace. No-op without OTel."""

    async def dispatch(self, request, call_next):
        token = None
        try:
            from opentelemetry import context as otel_context
            from opentelemetry.propagate import extract

            token = otel_context.attach(extract(dict(request.headers)))
        except Exception:
            token = None
        try:
            return await call_next(request)
        finally:
            if token is not None:
                try:
                    from opentelemetry import context as otel_context

                    otel_context.detach(token)
                except Exception:
                    pass


def build_agent_card(spec: AgentSpec, port: int) -> AgentCard:
    """AgentCard from the spec (name, description, version, skills)."""
    url = f"http://{Config.A2A_HOST}:{port}/"
    skills = [AgentSkill(id=s.id, name=s.name, description=s.description, tags=[spec.kind])
              for s in spec.a2a.skills]
    return AgentCard(
        name=spec.public_name,
        description=spec.description,
        version=spec.version,
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(url=url, protocol_binding="JSONRPC")],
        skills=skills,
    )


def build_starlette_app(agent, card: AgentCard, node: str = "") -> Starlette:
    started = _time.time()

    async def _health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "node": node or card.name, "agent": card.name,
                             "version": card.version, "uptime_seconds": round(_time.time() - started, 1)})

    handler = DefaultRequestHandler(
        # stream=False: output middleware (tipping-off, redaction) then sees the
        # complete answer before any byte leaves the node.
        agent_executor=A2AExecutor(agent),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return Starlette(
        middleware=[Middleware(TraceContextMiddleware)],
        routes=[
            Route("/health", _health, methods=["GET"]),
            *create_agent_card_routes(card),
            *create_jsonrpc_routes(handler, "/"),
        ],
    )


def serve(agent, card: AgentCard, port: int, node: str = "") -> None:
    uvicorn.run(build_starlette_app(agent, card, node), host=Config.A2A_HOST, port=port, log_level="warning")
