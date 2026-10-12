"""Single activation point for observability (MAF-native OpenTelemetry).

Call :func:`setup_observability` once per process, before agents are built, so
the Agent Framework emits its native spans (``invoke_agent``, ``chat``,
``execute_tool``, ``workflow.run``, ``executor.process``).

``OBS_PROFILE``:
  - ``dev``  -> ``configure_otel_providers()`` (console and/or OTLP, e.g. Aspire/Jaeger)
  - ``prod`` -> Azure Monitor (if installed + connection string), else dev fallback
  - ``off``  -> file logging only

Opik (``OPIK_ENABLED=true``) is layered on top by ``opik_setup`` and is optional.
"""
from __future__ import annotations

import logging
import os

from src.config import Config
from src.observability.logging_config import CAT_SYSTEM, configure_logging

_INITIALIZED = False


def _set_otel_env() -> None:
    os.environ.setdefault("OTEL_SERVICE_NAME", Config.OTEL_SERVICE_NAME)
    if Config.OTEL_EXPORTER_OTLP_ENDPOINT:
        os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", Config.OTEL_EXPORTER_OTLP_ENDPOINT)
    os.environ.setdefault("ENABLE_INSTRUMENTATION", str(Config.ENABLE_INSTRUMENTATION).lower())
    # PII minimisation (ISTR rule 4): only ever forward prompts/outputs to traces if opted in.
    os.environ.setdefault("ENABLE_SENSITIVE_DATA", str(Config.ENABLE_SENSITIVE_DATA).lower())
    if Config.ENABLE_CONSOLE_EXPORTERS:
        os.environ.setdefault("ENABLE_CONSOLE_EXPORTERS", "true")


def _ensure_composite_propagator() -> None:
    """TraceContext + Baggage so case/user identity crosses A2A hops."""
    try:
        from opentelemetry.baggage.propagation import W3CBaggagePropagator
        from opentelemetry.propagate import set_global_textmap
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        set_global_textmap(CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()]))
    except Exception:
        pass


def _instrument_httpx(log: logging.Logger) -> None:
    """Injects traceparent/baggage on A2AAgent's own httpx client (never replace that client)."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        log.info("httpx instrumentation enabled (A2A trace propagation active).")
    except Exception as exc:
        log.info("httpx instrumentation unavailable (%s); install opentelemetry-instrumentation-httpx "
                 "for cross-process trace linking.", exc)


def _setup_dev(log: logging.Logger) -> None:
    from agent_framework.observability import configure_otel_providers

    configure_otel_providers(enable_sensitive_data=Config.ENABLE_SENSITIVE_DATA)
    log.info("Observability profile=dev endpoint=%s sensitive=%s",
             Config.OTEL_EXPORTER_OTLP_ENDPOINT, Config.ENABLE_SENSITIVE_DATA)


def _setup_prod(log: logging.Logger) -> None:
    conn = Config.APPLICATIONINSIGHTS_CONNECTION_STRING
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except ImportError:
        configure_azure_monitor = None
    if not conn or configure_azure_monitor is None:
        log.warning("OBS_PROFILE=prod but Azure Monitor is not configured/installed; using dev providers.")
        _setup_dev(log)
        return
    from agent_framework.observability import create_resource, enable_instrumentation

    configure_azure_monitor(connection_string=conn, resource=create_resource())
    enable_instrumentation(enable_sensitive_data=Config.ENABLE_SENSITIVE_DATA)
    log.info("Observability profile=prod: Azure Monitor active.")


def setup_observability(service_name: str | None = None) -> None:
    """Idempotent, crash-safe activation of logging + OTel (+ Opik when enabled)."""
    global _INITIALIZED
    if _INITIALIZED:
        return
    if service_name:
        os.environ["OTEL_SERVICE_NAME"] = service_name
        Config.OTEL_SERVICE_NAME = service_name
    configure_logging(service_name)
    log = logging.getLogger(CAT_SYSTEM)

    profile = (Config.OBS_PROFILE or "off").lower()
    if profile != "off":
        _set_otel_env()
        try:
            _setup_prod(log) if profile == "prod" else _setup_dev(log)
        except Exception as exc:  # telemetry must never take a node down
            log.warning("OTel activation failed (%s); continuing with logging only.", exc)
        _ensure_composite_propagator()
        _instrument_httpx(log)
    else:
        log.info("Observability profile=off: file logging only.")

    from src.observability.opik_setup import setup_opik

    setup_opik(service_name or Config.OTEL_SERVICE_NAME)
    _INITIALIZED = True
