"""Centralised, trace-correlated logging (lean port of the reference module).

Every record is stamped with trace_id / span_id / case_id from the active
OpenTelemetry context, so a log line can be joined to its distributed trace and
to the case under investigation. Import-safe; never raises.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
from typing import Any

from src.config import Config

CAT_AGENT = "mesh.agent"
CAT_WORKFLOW = "mesh.workflow"
CAT_TOOLS = "mesh.tools"
CAT_A2A = "mesh.a2a"
CAT_MCP = "mesh.mcp"
CAT_FACTORY = "mesh.factory"
CAT_SYSTEM = "mesh.system"

_CONFIGURED = False


class TraceContextFilter(logging.Filter):
    """Adds trace_id, span_id, request_id and case_id to each record (``-`` when absent)."""

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = span_id = request_id = case_id = "-"
        try:
            from opentelemetry import baggage, trace

            ctx = trace.get_current_span().get_span_context()
            if getattr(ctx, "is_valid", False):
                trace_id, span_id = format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
            request_id = baggage.get_baggage("istr.request_id") or "-"
            case_id = baggage.get_baggage("istr.case_id") or "-"
        except Exception:
            pass
        record.trace_id, record.span_id = trace_id, span_id
        record.request_id, record.case_id = request_id, case_id
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "-"),
            "span_id": getattr(record, "span_id", "-"),
            "request_id": getattr(record, "request_id", "-"),
            "case_id": getattr(record, "case_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(service_name: str | None = None) -> None:
    """Installs one rotating file sink + console sink on the ``mesh`` logger tree."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    try:
        root = logging.getLogger("mesh")
        root.setLevel(Config.LOG_LEVEL)
        root.propagate = False
        fmt = (
            _JsonFormatter()
            if Config.LOG_JSON
            else logging.Formatter(
                f"%(asctime)s %(levelname)-7s [{service_name or Config.OTEL_SERVICE_NAME}] %(name)s "
                "case=%(case_id)s trace=%(trace_id)s | %(message)s"
            )
        )
        flt = TraceContextFilter()
        path = Config.resolve_path(Config.LOG_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        for h in (fh, ch):
            h.setFormatter(fmt)
            h.addFilter(flt)
            root.addHandler(h)
    except Exception:
        pass
    _CONFIGURED = True


def get_logger(category: str) -> logging.Logger:
    """Category logger. Adds the trace filter so records format even before setup."""
    log = logging.getLogger(category)
    if not any(isinstance(f, TraceContextFilter) for f in log.filters):
        log.addFilter(TraceContextFilter())
    return log
