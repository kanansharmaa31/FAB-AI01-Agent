"""A2A client helpers: call a remote mesh node by name.

``ask_remote`` is the single transport seam: the workflow and the peer tools
call it through a module attribute, so the offline tests patch it
(``patch.object(orchestrator, "ask_remote", fake)``) exactly as in the reference.

Trace propagation: we do not replace ``A2AAgent``'s HTTP client (that breaks A2A
response parsing). httpx OpenTelemetry instrumentation, enabled in
``setup_observability``, injects traceparent + baggage instead.
"""
from __future__ import annotations

import time

import httpx
from agent_framework.a2a import A2AAgent

from src.config import Config
from src.observability import CAT_A2A, get_logger

_log = get_logger(CAT_A2A)


def get_remote_agent(name: str) -> A2AAgent:
    return A2AAgent(
        name=name,
        url=Config.agent_url(name),
        supported_protocol_bindings=["JSONRPC"],
        timeout=httpx.Timeout(connect=10.0, read=Config.A2A_TIMEOUT, write=10.0, pool=5.0),
    )


async def ask_remote(name: str, prompt: str, **_: object) -> str:
    """Sends ``prompt`` to node ``name`` and returns its text answer. Raises on transport failure."""
    t0 = time.perf_counter()
    try:
        res = await get_remote_agent(name).run(prompt)
        text = getattr(res, "text", None) or str(res)
        _log.info("A2A node=%s ok %dms chars=%d", name, int((time.perf_counter() - t0) * 1000), len(text))
        return text
    except Exception as exc:
        _log.error("A2A node=%s failed: %s", name, exc)
        raise
