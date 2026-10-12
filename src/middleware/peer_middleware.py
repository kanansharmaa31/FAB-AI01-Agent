"""Middleware supporting agent-as-tool delegation (auto-added by the factory).

- :class:`MeshContextMiddleware` (every agent): restores mesh context from the
  inbound A2A prompt headers (``[peer_depth=N]``, ``[case_id=...]``,
  ``[request_id=...]``). Depth feeds the cross-process loop guard. Case and
  request IDs are attached as baggage for the run, so audit records on every
  node are correlated with the originating request even when OpenTelemetry
  propagation is not installed.
- :class:`ReasoningRelayMiddleware` (agents with a2a_peer tools): re-emits the
  peers' ``<llm_reasoning>`` blocks on this agent's final answer, so one A2A
  response carries every agent's reasoning back to the workflow.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

from agent_framework import AgentContext, AgentMiddleware

from src.middleware._util import last_user_text, transform_final_text
from src.observability import baggage
from src.tools import collaboration_tools as ct

_HEADER = re.compile(r"^\[(case_id|request_id)=([A-Za-z0-9_.:-]{1,64})\]\s*$", re.MULTILINE)


class MeshContextMiddleware(AgentMiddleware):
    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        text = last_user_text(context)
        headers = {k: v for k, v in _HEADER.findall(text) if not baggage.get(k)}
        bag_token = baggage.set_request_baggage(**headers) if headers else None
        m = ct.DEPTH_HEADER.search(text)
        depth_token = ct.set_depth(int(m.group(1))) if m else None
        try:
            await call_next()
        finally:
            if depth_token is not None:
                ct.reset_depth(depth_token)
            baggage.detach_baggage(bag_token)


class ReasoningRelayMiddleware(AgentMiddleware):
    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        entries, token = ct.start_reasoning_collection()
        try:
            await call_next()
        finally:
            ct.stop_reasoning_collection(token)

        def _append(text: str) -> str | None:
            if not entries:
                return None
            blocks = "\n".join(
                "<llm_reasoning>" + json.dumps({**e.get("data", {}), "agent": e.get("agent"),
                                                "phase": e.get("phase")}) + "</llm_reasoning>"
                for e in entries
            )
            return f"{text}\n{blocks}"

        transform_final_text(context, _append)
