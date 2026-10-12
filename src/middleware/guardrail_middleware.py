"""Per-agent deterministic guardrails and PII redaction, driven by ``spec.guardrails``.

- :class:`GuardrailMiddleware`: input checks short-circuit the run (the LLM never
  sees a blocked prompt); output checks replace a violating answer.
- :class:`PIIRedactionMiddleware`: redacts PII in the final answer.

These complement, not replace, the workflow-level gates: an agent called
directly over A2A (bypassing the workflow) is still protected.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_framework import AgentContext, AgentMiddleware

from src.guardrails.deterministic_filters import redact_pii, run_checks
from src.middleware._util import last_user_text, short_circuit, transform_final_text
from src.observability import CAT_AGENT, get_logger

_log = get_logger(CAT_AGENT)

INPUT_BLOCK_MARKER = "GUARDRAIL_BLOCKED_INPUT"
OUTPUT_BLOCK_MARKER = "GUARDRAIL_BLOCKED_OUTPUT"


class GuardrailMiddleware(AgentMiddleware):
    def __init__(self, node: str, input_checks: list[str], output_checks: list[str]):
        self.node = node
        self.input_checks = list(input_checks)
        self.output_checks = list(output_checks)

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        if self.input_checks:
            res = run_checks(last_user_text(context), self.input_checks)
            if not res.allowed:
                _log.warning("input guardrail BLOCK node=%s categories=%s", self.node, res.categories)
                short_circuit(context, f"{INPUT_BLOCK_MARKER}: request refused by deterministic guardrails "
                                       f"({', '.join(res.categories)}).")
                return
        await call_next()
        if not self.output_checks:
            return

        def _check(text: str) -> str | None:
            res = run_checks(text, self.output_checks)
            if res.allowed:
                return None
            _log.warning("output guardrail BLOCK node=%s categories=%s", self.node, res.categories)
            return (f"{OUTPUT_BLOCK_MARKER}: the {self.node} agent's output was withheld "
                    f"({', '.join(res.categories)}). Rework required before MLRO review.")

        transform_final_text(context, _check)


class PIIRedactionMiddleware(AgentMiddleware):
    def __init__(self, node: str):
        self.node = node

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        await call_next()

        def _redact(text: str) -> str | None:
            red = redact_pii(text)
            return None if red == text else red

        transform_final_text(context, _redact)
