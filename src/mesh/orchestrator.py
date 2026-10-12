"""Mesh client orchestrator: runs one investigation request through the workflow.

Public surface: :func:`handle_request`, :func:`handle_request_stream`,
:class:`InvestigationResult`, and the module-level ``ask_remote`` seam that the
offline tests patch (same pattern as the reference).
"""
from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from src.a2a.clients import ask_remote
from src.auth.identity_provider import User
from src.config import Config
from src.memory import ConversationStore
from src.mesh import events
from src.mesh.workflow import CaseState, build_istr_workflow
from src.observability import CAT_SYSTEM, baggage, get_logger

_log = get_logger(CAT_SYSTEM)

_CASE_RE = re.compile(r"\bCASE-\d{3,}\b", re.IGNORECASE)


@dataclass
class InvestigationResult:
    request_id: str
    case_id: str
    session_id: str
    status: str
    summary: str
    draft: dict[str, Any] | None
    blocked: bool = False
    block_stage: str | None = None
    trail: list[str] = field(default_factory=list)
    reasoning: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _final_state(run_result: Any) -> CaseState | None:
    try:
        for out in reversed(run_result.get_outputs()):
            if isinstance(out, CaseState):
                return out
    except Exception:
        pass
    return None


async def handle_request(user: User, query: str = "", case_id: str | None = None, session_id: str | None = None,
                         request_id: str | None = None) -> InvestigationResult:
    request_id = request_id or uuid.uuid4().hex[:12].upper()
    m = _CASE_RE.search(query or "")
    case_id = (case_id or (m.group(0) if m else "")).upper()
    session_id = session_id or f"{user.username}_{uuid.uuid4().hex[:8]}"
    query = query or f"Investigate case {case_id} and prepare a draft ISTR for MLRO review."

    token = baggage.set_request_baggage(request_id=request_id, case_id=case_id, session_id=session_id,
                                        user=user.username, role=user.role)
    store = ConversationStore() if Config.ENABLE_CONVERSATION_MEMORY else None
    mem_key = ConversationStore.key(case_id, session_id)
    state = CaseState(user_name=user.username, role=user.role, query=query, case_id=case_id,
                      session_id=session_id, request_id=request_id)
    try:
        if store:
            try:
                state.conversation_history = store.load(mem_key, Config.CONVERSATION_MAX_TURNS)
            except Exception as exc:  # memory must never break a request
                _log.warning("memory load failed key=%s: %s", mem_key, exc)
        events.stage(state, "request", "started", "Request received", case_id=case_id, request_id=request_id)
        workflow = build_istr_workflow(ask=ask_remote)  # module attr -> patchable in tests
        final = _final_state(await workflow.run(state)) or state
    finally:
        baggage.detach_baggage(token)

    if final is state and not final.blocked and final.status != "PENDING_MLRO_REVIEW":
        final.blocked, final.block_stage, final.status = True, "internal_error", "ERROR"
        final.summary = "Internal error: the workflow produced no output."

    if store and not final.blocked and final.summary:
        try:
            store.append_turn(mem_key, query, final.summary)
        except Exception as exc:
            _log.warning("memory save failed key=%s: %s", mem_key, exc)

    return InvestigationResult(
        request_id=request_id, case_id=case_id, session_id=session_id, status=final.status,
        summary=final.summary, draft=final.draft, blocked=final.blocked, block_stage=final.block_stage,
        trail=final.trail, reasoning=final.reasoning, events=final.events,
    )


async def handle_request_stream(user: User, query: str = "", case_id: str | None = None,
                                session_id: str | None = None, request_id: str | None = None,
                                event_queue: asyncio.Queue | None = None) -> InvestigationResult:
    """Like :func:`handle_request`, pushing stage events to ``event_queue``; ``None`` marks EOF."""
    token = events.stream_queue.set(event_queue) if event_queue is not None else None
    try:
        return await handle_request(user, query, case_id, session_id, request_id)
    finally:
        if token is not None:
            events.stream_queue.reset(token)
        if event_queue is not None:
            event_queue.put_nowait(None)
