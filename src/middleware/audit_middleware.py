"""JSONL audit trail for every agent run and every tool call (ISTR rule 4).

Two middleware share one writer:
- :class:`AuditMiddleware` (agent level): inputs, final output, latency, tokens.
- :class:`AuditToolMiddleware` (function level): tool name, arguments, result preview.

Each record is correlated with the distributed trace (trace_id/span_id) and the
originating request via W3C baggage (request_id, case_id, user, role), so an
auditor can reconstruct a case end to end across nodes.

PII minimisation: unless ``ENABLE_SENSITIVE_DATA=true``, text is passed through
deterministic PII redaction and truncated before it is written.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from agent_framework import AgentContext, AgentMiddleware, FunctionInvocationContext, FunctionMiddleware

from src.config import Config
from src.guardrails.deterministic_filters import redact_pii
from src.middleware._util import input_text, response_text, transform_final_text
from src.observability import CAT_AGENT, CAT_TOOLS, baggage, get_logger

_log = get_logger(CAT_AGENT)
_tool_log = get_logger(CAT_TOOLS)
_lock = threading.Lock()
_MAX_CHARS = 2000


def _trace_ids() -> tuple[str, str]:
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if getattr(ctx, "is_valid", False):
            return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    except Exception:
        pass
    return "-", "-"


def scrub(text: Any) -> str:
    """Redact + truncate unless sensitive-data logging is explicitly enabled."""
    s = text if isinstance(text, str) else json.dumps(text, default=str, ensure_ascii=False)
    if Config.ENABLE_SENSITIVE_DATA:
        return s
    s = redact_pii(s)
    return s if len(s) <= _MAX_CHARS else s[:_MAX_CHARS] + f"...[truncated {len(s) - _MAX_CHARS} chars]"


class AuditWriter:
    """Append-only JSONL writer (thread-safe within a process)."""

    def __init__(self, log_path: str | None = None):
        self.path = Config.resolve_path(log_path or Config.AUDIT_LOG_FILE)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        trace_id, span_id = _trace_ids()
        base = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "span_id": span_id,
            "request_id": baggage.get("request_id") or "-",
            "case_id": baggage.get("case_id") or "-",
            "session_id": baggage.get("session_id") or "-",
            "user": baggage.get("user") or "-",
            "role": baggage.get("role") or "-",
        }
        try:
            with _lock, self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({**base, **record}, ensure_ascii=False, default=str) + "\n")
        except Exception:  # auditing must never break the agent; failure is logged instead
            _log.exception("audit write failed")


class AuditMiddleware(AgentMiddleware):
    """Agent-run audit record (one per invocation)."""

    def __init__(self, node: str, log_path: str | None = None):
        self.node = node
        self.writer = AuditWriter(log_path)

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        t0 = time.perf_counter()
        agent_name = getattr(context.agent, "name", self.node)
        inputs = scrub(input_text(context))

        def _record(status: str, output: str, error: str | None = None, usage: Any = None) -> None:
            rec: dict[str, Any] = {
                "event": "agent_run",
                "node": self.node,
                "agent_name": agent_name,
                "status": status,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "input": inputs,
                "output": scrub(output),
            }
            if usage is not None:
                rec["input_tokens"] = getattr(usage, "input_token_count", None)
                rec["output_tokens"] = getattr(usage, "output_token_count", None)
            if error:
                rec["error"] = error
            self.writer.write(rec)
            _log.info("agent=%s status=%s latency_ms=%s", self.node, status, rec["latency_ms"])

        try:
            await call_next()
        except Exception as exc:
            _record("ERROR", "", error=str(exc))
            raise

        if context.stream:
            def _on_final(text: str) -> None:
                _record("SUCCESS", text)
                return None

            transform_final_text(context, _on_final)
        else:
            _record("SUCCESS", response_text(context.result), usage=getattr(context.result, "usage_details", None))


class AuditToolMiddleware(FunctionMiddleware):
    """Tool-call audit record (one per function / MCP / A2A-peer tool invocation)."""

    def __init__(self, node: str, log_path: str | None = None):
        self.node = node
        self.writer = AuditWriter(log_path)

    async def process(self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
        t0 = time.perf_counter()
        status, error = "SUCCESS", None
        try:
            await call_next()
        except Exception as exc:
            status, error = "ERROR", str(exc)
            raise
        finally:
            args = context.arguments
            if hasattr(args, "model_dump"):
                args = args.model_dump()
            result = context.result
            if isinstance(result, list):
                result = " ".join(str(getattr(c, "text", None) or c) for c in result)
            rec: dict[str, Any] = {
                "event": "tool_call",
                "node": self.node,
                "tool": context.function.name,
                "status": status,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "arguments": scrub(args),
                "result_preview": scrub(result if result is not None else "")[:500],
            }
            if error:
                rec["error"] = error
            self.writer.write(rec)
            _tool_log.info("node=%s tool=%s status=%s", self.node, context.function.name, status)
