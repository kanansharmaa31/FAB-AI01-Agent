"""Optional Opik adapter for LLM-level tracing.

Why OTLP rather than an SDK wrapper: MAF already emits GenAI-semantic-convention
spans (``invoke_agent``, ``chat``, ``execute_tool``) with token usage. Opik can
ingest OpenTelemetry directly, so we add one extra span exporter pointed at
Opik's OTLP endpoint. That captures every agent, LLM and tool call in every node
without touching agent code or locking us to Opik's SDK.

Enabled with ``OPIK_ENABLED=true``. Everything is import-guarded: if Opik or the
OTLP/HTTP exporter is not installed, a warning is logged and the mesh runs as usual.

Endpoints:
  - Opik Cloud:   https://www.comet.com/opik/api/v1/private/otel
  - Self-hosted:  ``OPIK_URL_OVERRIDE`` + /v1/private/otel (e.g. http://localhost:5173/api)
"""
from __future__ import annotations

import logging
from typing import Any, Callable, TypeVar

from src.config import Config
from src.observability.logging_config import CAT_SYSTEM

_log = logging.getLogger(CAT_SYSTEM)
_ENABLED = False

F = TypeVar("F", bound=Callable[..., Any])

_CLOUD_OTEL = "https://www.comet.com/opik/api/v1/private/otel"


def opik_otel_endpoint() -> str:
    base = Config.OPIK_URL_OVERRIDE.rstrip("/")
    return f"{base}/v1/private/otel" if base else _CLOUD_OTEL


def opik_headers() -> dict[str, str]:
    headers = {"projectName": Config.OPIK_PROJECT_NAME}
    if Config.OPIK_API_KEY:
        headers["Authorization"] = Config.OPIK_API_KEY
    if Config.OPIK_WORKSPACE:
        headers["Comet-Workspace"] = Config.OPIK_WORKSPACE
    return headers


def setup_opik(service_name: str) -> bool:
    """Adds an Opik OTLP exporter to the active TracerProvider. Returns True when active."""
    global _ENABLED
    if not Config.OPIK_ENABLED or _ENABLED:
        return _ENABLED
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        _log.warning("OPIK_ENABLED=true but OTLP/HTTP exporter missing (%s). "
                     "pip install opentelemetry-exporter-otlp-proto-http", exc)
        return False

    try:
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):  # OBS_PROFILE=off -> create one for Opik only
            provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
            trace.set_tracer_provider(provider)
            try:
                from agent_framework.observability import enable_instrumentation

                enable_instrumentation(enable_sensitive_data=Config.ENABLE_SENSITIVE_DATA)
            except Exception:
                pass
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{opik_otel_endpoint()}/v1/traces",
                                                headers=opik_headers()))
        )
        _ENABLED = True
        _log.info("Opik tracing active -> %s (project=%s)", opik_otel_endpoint(), Config.OPIK_PROJECT_NAME)
    except Exception as exc:
        _log.warning("Opik setup failed (%s); continuing without Opik.", exc)
    return _ENABLED


def track(name: str | None = None) -> Callable[[F], F]:
    """``opik.track`` when the SDK is installed and enabled, otherwise a no-op decorator.

    Use for non-MAF helpers you want visible in Opik (e.g. a scoring function).
    """
    def _decorator(fn: F) -> F:
        if not Config.OPIK_ENABLED:
            return fn
        try:
            import opik

            return opik.track(name=name or fn.__name__)(fn)  # type: ignore[return-value]
        except Exception:
            return fn

    return _decorator
