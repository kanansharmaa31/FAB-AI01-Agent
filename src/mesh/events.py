"""Stage events for the live workflow view (SSE) and the run record.

Same mechanism as the reference: a ContextVar holds the active SSE queue, so any
executor can emit without signature changes; it is a no-op when not streaming.
Every event is also appended to ``CaseState.events`` so non-streaming callers get
the full trace in the response.

Event shapes (stable contract for the UI team)::

    {"event_type": "stage", "stage": "<executor id>", "status": "started|completed|blocked|failed|pending_review",
     "message": str, "ts": iso8601, ...extra}
    {"event_type": "reasoning", "entries": [{"agent", "phase", "data", "timestamp"}], "ts": iso8601}
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

stream_queue: ContextVar[asyncio.Queue | None] = ContextVar("istr_stream_queue", default=None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(state: Any, event: dict[str, Any]) -> None:
    event = {"ts": _now(), **event}
    events = getattr(state, "events", None)
    if isinstance(events, list):
        events.append(event)
    q = stream_queue.get()
    if q is not None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def stage(state: Any, name: str, status: str, message: str = "", **extra: Any) -> None:
    emit(state, {"event_type": "stage", "stage": name, "status": status, "message": message, **extra})


def reasoning(state: Any, entries: list[dict[str, Any]]) -> None:
    if entries:
        emit(state, {"event_type": "reasoning", "entries": entries})
