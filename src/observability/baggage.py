"""W3C baggage: carries request identity (request/case/session/user/role) across A2A hops.

Set once at the orchestrator entry point. httpx instrumentation (when installed)
injects ``traceparent`` + ``baggage`` headers on every outbound A2A call, and
``src/a2a/hosting.py`` extracts them on the receiving node, so audit records on
remote nodes carry the originating case and user.
"""
from __future__ import annotations

KEYS: dict[str, str] = {
    "request_id": "istr.request_id",
    "case_id": "istr.case_id",
    "session_id": "istr.session_id",
    "user": "istr.user",
    "role": "istr.role",
}


def set_request_baggage(**values: str) -> object | None:
    """Attaches the given identity values; returns a token for :func:`detach_baggage`."""
    try:
        from opentelemetry import baggage
        from opentelemetry import context as otel_context

        ctx = otel_context.get_current()
        for k, v in values.items():
            if k in KEYS and v:
                ctx = baggage.set_baggage(KEYS[k], str(v), context=ctx)
        return otel_context.attach(ctx)
    except Exception:
        return None


def detach_baggage(token: object | None) -> None:
    if token is None:
        return
    try:
        from opentelemetry import context as otel_context

        otel_context.detach(token)
    except Exception:
        pass


def get(key: str) -> str | None:
    """Reads one identity value (``case_id``, ``user`` ...) from the active context."""
    try:
        from opentelemetry import baggage

        return baggage.get_baggage(KEYS[key]) or None
    except Exception:
        return None
