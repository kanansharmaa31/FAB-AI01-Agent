"""Blocks customer-facing notification language in agent outputs (ISTR rule 3).

Tipping off a subject that an internal report or investigation exists is a
regulatory offence, so this check is deterministic and runs on *every* agent
by default (``middleware: [audit, tipping_off]``). It does not depend on the
model following its prompt.

A blocked output is replaced wholesale, not edited: a partially-redacted
sentence could still tip off. The original text goes only to the (redacted)
audit trail via AuditMiddleware, which sits outside this middleware.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_framework import AgentContext, AgentMiddleware

from src.guardrails.deterministic_filters import detect_tipping_off
from src.middleware._util import transform_final_text
from src.observability import CAT_AGENT, get_logger

_log = get_logger(CAT_AGENT)

BLOCK_MARKER = "TIPPING_OFF_BLOCKED"


def blocked_message(node: str, violations: list[str]) -> str:
    return (
        f"{BLOCK_MARKER}: the {node} agent's output was withheld because it contained language that could "
        "inform the subject of an internal report or investigation. The analyst has been notified via the "
        f"audit trail. Detected: {'; '.join(violations)[:200]}"
    )


class TippingOffMiddleware(AgentMiddleware):
    def __init__(self, node: str):
        self.node = node

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        await call_next()

        def _check(text: str) -> str | None:
            violations = detect_tipping_off(text)
            if not violations:
                return None
            _log.warning("tipping_off BLOCK node=%s: %s", self.node, violations)
            return blocked_message(self.node, violations)

        transform_final_text(context, _check)
