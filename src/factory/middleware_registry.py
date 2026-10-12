"""Middleware by name: spec ``middleware: [audit, tipping_off, ...]`` -> MAF middleware objects.

Public names (usable in specs):

=================  =============================================================
``audit``          JSONL audit of every agent run + every tool call (trace-correlated)
``tipping_off``    blocks customer-facing notification language in outputs
``pii_redaction``  redacts PII in the final answer
``token_budget``   circuit breaker: max tool calls per run (``spec.max_tool_calls``)
=================  =============================================================

The factory also adds, automatically and outside spec control:
``MeshContextMiddleware`` (all agents), ``GuardrailMiddleware`` (from
``spec.guardrails``), ``PIIRedactionMiddleware`` (when
``guardrails.pii_redaction``) and ``ReasoningRelayMiddleware`` (agents with
``a2a_peer`` tools).

Ordering: MAF runs the first middleware outermost. ``audit`` is listed first so
it records what actually left the agent, after every inner middleware ran.
"""
from __future__ import annotations

from typing import Any, Callable

from src.factory.spec import AgentSpec
from src.middleware.audit_middleware import AuditMiddleware, AuditToolMiddleware
from src.middleware.budget_middleware import ToolBudgetFunctionMiddleware, ToolBudgetRunMiddleware
from src.middleware.guardrail_middleware import GuardrailMiddleware, PIIRedactionMiddleware
from src.middleware.peer_middleware import MeshContextMiddleware, ReasoningRelayMiddleware
from src.middleware.tipping_off_middleware import TippingOffMiddleware

MiddlewareBuilder = Callable[[AgentSpec, "str | None"], list[Any]]

MIDDLEWARE_BUILDERS: dict[str, MiddlewareBuilder] = {
    "audit": lambda spec, log_path: [AuditMiddleware(spec.name, log_path), AuditToolMiddleware(spec.name, log_path)],
    "tipping_off": lambda spec, _: [TippingOffMiddleware(spec.name)],
    "pii_redaction": lambda spec, _: [PIIRedactionMiddleware(spec.name)],
    "token_budget": lambda spec, _: [ToolBudgetRunMiddleware(spec.max_tool_calls),
                                     ToolBudgetFunctionMiddleware(spec.name, spec.max_tool_calls)],
}


def build_middleware(spec: AgentSpec, log_path: str | None = None) -> list[Any]:
    """Full, ordered middleware stack for an agent."""
    unknown = [n for n in spec.middleware if n not in MIDDLEWARE_BUILDERS]
    if unknown:
        raise ValueError(f"Unknown middleware {unknown} for agent '{spec.name}'. "
                         f"Known: {sorted(MIDDLEWARE_BUILDERS)}")
    stack: list[Any] = [MeshContextMiddleware()]
    for name in spec.middleware:
        stack.extend(MIDDLEWARE_BUILDERS[name](spec, log_path))
    g = spec.guardrails
    if g.input or g.output:
        stack.append(GuardrailMiddleware(spec.name, g.input, g.output))
    if g.pii_redaction and "pii_redaction" not in spec.middleware:
        stack.append(PIIRedactionMiddleware(spec.name))
    if spec.peer_tools:
        stack.append(ReasoningRelayMiddleware())
    return stack
