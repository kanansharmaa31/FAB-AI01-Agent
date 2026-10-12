"""Agent-as-tool over A2A: ``a2a_peer`` tool refs become ``consult_<node>`` tools.

Generalises the reference's hand-written ``query_structured_data`` /
``query_knowledge_base`` into a factory driven by the spec. Safeguards:

1. Depth guard. A ContextVar bounds nested delegation. Unlike the reference
   (in-process only), the depth also crosses process boundaries: the outbound
   prompt carries ``[peer_depth=N]`` (plus ``[case_id=..]``/``[request_id=..]``
   for audit correlation) that ``MeshContextMiddleware`` on the receiving node
   restores, so A -> B -> A loops stop even across nodes.
2. Soft fail (ISTR rule 7). A failed hop returns ``<NODE>_UNAVAILABLE: ...``
   rather than raising, so the coordinator can state which evidence is missing.
3. Per-request cache. The same (node, question) is fetched once per run.
4. Reasoning relay. ``<llm_reasoning>`` blocks in peer answers are stripped
   (saves coordinator tokens) and collected; ``ReasoningRelayMiddleware``
   re-emits them on the coordinator's final answer so the workflow and UI see
   every agent's reasoning from a single A2A response.
"""
from __future__ import annotations

import asyncio
import contextvars
import re
import time

from agent_framework import FunctionTool, tool

from src.a2a.clients import ask_remote
from src.observability import CAT_A2A, baggage, get_logger
from src.tracing.llm_reasoning import extract_reasoning

_log = get_logger(CAT_A2A)

_peer_depth: contextvars.ContextVar[int] = contextvars.ContextVar("peer_call_depth", default=0)
_peer_cache: contextvars.ContextVar[dict | None] = contextvars.ContextVar("peer_cache", default=None)
_peer_reasoning: contextvars.ContextVar[list | None] = contextvars.ContextVar("peer_reasoning", default=None)

DEPTH_HEADER = re.compile(r"^\[peer_depth=(\d+)\]\s*", re.MULTILINE)

_A2A_RETRIES = 1
_A2A_RETRY_DELAY = 0.75


def unavailable_label(node: str) -> str:
    return f"{node.upper()}_UNAVAILABLE"


def current_depth() -> int:
    return _peer_depth.get()


def set_depth(depth: int) -> contextvars.Token[int]:
    return _peer_depth.set(depth)


def reset_depth(token: contextvars.Token[int]) -> None:
    _peer_depth.reset(token)


def start_reasoning_collection() -> tuple[list, contextvars.Token]:
    entries: list = []
    return entries, _peer_reasoning.set(entries)


def stop_reasoning_collection(token: contextvars.Token) -> None:
    _peer_reasoning.reset(token)


async def consult_peer(node: str, question: str, max_depth: int = 2) -> str:
    """Asks a peer node over A2A. Never raises: failures become ``<NODE>_UNAVAILABLE`` strings."""
    depth = _peer_depth.get()
    if depth >= max_depth:
        return f"PEER_LIMIT: delegation depth {depth} reached max {max_depth} for '{node}'; not delegating."

    cache = _peer_cache.get()
    if cache is None:
        cache = {}
        _peer_cache.set(cache)
    key = (node, question.strip())
    if key in cache:
        return cache[key]

    header = f"[peer_depth={depth + 1}]\n"
    for ctx_key in ("case_id", "request_id"):
        if value := baggage.get(ctx_key):
            header += f"[{ctx_key}={value}]\n"
    prompt = header + question

    token = _peer_depth.set(depth + 1)
    t0 = time.perf_counter()
    try:
        last_exc: Exception | None = None
        result: str | None = None
        for attempt in range(_A2A_RETRIES + 1):
            try:
                result = await ask_remote(node, prompt)
                break
            except Exception as exc:  # transport failure: retry once, then soft-fail
                last_exc = exc
                if attempt < _A2A_RETRIES:
                    await asyncio.sleep(_A2A_RETRY_DELAY)
        if result is None:
            raise last_exc or RuntimeError("no response")

        entries, clean = extract_reasoning(result, node)
        if entries:
            collected = _peer_reasoning.get()
            if collected is not None:
                collected.extend(e.to_dict() for e in entries)
        result = clean or result
        if result.strip() == question.strip():
            result = f"{unavailable_label(node)}: peer echoed the request without evidence."
        cache[key] = result
        return result
    except Exception as exc:
        _log.warning("peer '%s' unavailable: %s", node, exc)
        result = f"{unavailable_label(node)}: could not reach the {node} agent ({exc})."
        cache[key] = result
        return result
    finally:
        _peer_depth.reset(token)
        try:
            from opentelemetry import trace

            span = trace.get_current_span()
            if span and span.is_recording():
                span.set_attribute("peer.target_node", node)
                span.set_attribute("peer.depth", depth + 1)
                span.set_attribute("peer.duration_ms", int((time.perf_counter() - t0) * 1000))
        except Exception:
            pass


def make_peer_tool(node: str, description: str, max_depth: int = 2) -> FunctionTool:
    """Builds the ``consult_<node>`` tool for an ``a2a_peer`` ToolRef."""

    async def _consult(question: str) -> str:
        return await consult_peer(node, question, max_depth=max_depth)

    _consult.__name__ = f"consult_{node}"
    return tool(
        _consult,
        name=f"consult_{node}",
        description=(
            f"Delegate to the {node} agent over A2A. {description} "
            "Pass one self-contained question including the case_id and customer_id. "
            f"If it returns {unavailable_label(node)}, record that evidence as missing; do not guess."
        ),
    )
