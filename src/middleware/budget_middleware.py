"""Tool-call circuit breaker (``token_budget`` middleware).

A model that loops on tool calls burns tokens and hammers downstream services.
This caps tool calls per agent run (default 15, ``spec.max_tool_calls``).

Two cooperating middleware, because MAF scopes them differently:
- :class:`ToolBudgetRunMiddleware` (agent level) opens a fresh budget per run.
- :class:`ToolBudgetFunctionMiddleware` (function level) counts calls against it.

The budget lives in a ContextVar holding a *mutable* counter so concurrent
requests on the same agent instance never share a budget, while parallel tool
calls inside one run (which copy the context) still share one.

When exceeded, the call is short-circuited and the tool loop is terminated with
``MiddlewareTermination``; the agent returns what it has, and the factory also
sets MAF's native ``max_function_calls`` as a hard backstop.
"""
from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent_framework import (
    AgentContext,
    AgentMiddleware,
    FunctionInvocationContext,
    FunctionMiddleware,
    MiddlewareTermination,
)

from src.observability import CAT_TOOLS, get_logger

_log = get_logger(CAT_TOOLS)

BUDGET_MARKER = "TOOL_BUDGET_EXCEEDED"


@dataclass
class _Budget:
    limit: int
    used: int = 0


_budget: contextvars.ContextVar[_Budget | None] = contextvars.ContextVar("tool_budget", default=None)


class ToolBudgetRunMiddleware(AgentMiddleware):
    def __init__(self, limit: int):
        self.limit = limit

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        token = _budget.set(_Budget(self.limit))
        try:
            await call_next()
        finally:
            _budget.reset(token)


class ToolBudgetFunctionMiddleware(FunctionMiddleware):
    def __init__(self, node: str, limit: int):
        self.node = node
        self.limit = limit

    async def process(self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
        budget = _budget.get()
        if budget is None:  # run middleware absent (e.g. direct tool use): budget per call chain
            budget = _Budget(self.limit)
            _budget.set(budget)
        budget.used += 1
        if budget.used > budget.limit:
            msg = (f"{BUDGET_MARKER}: {self.node} reached its limit of {budget.limit} tool calls for this run. "
                   "Stop calling tools and answer with the evidence gathered so far, listing what is missing.")
            _log.warning("circuit breaker OPEN node=%s used=%d limit=%d tool=%s",
                         self.node, budget.used, budget.limit, context.function.name)
            context.result = msg
            raise MiddlewareTermination(msg, result=msg)
        await call_next()
