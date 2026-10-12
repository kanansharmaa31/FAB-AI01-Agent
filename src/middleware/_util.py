"""Helpers shared by the agent middleware.

MAF hands agent middleware either an ``AgentResponse`` (``stream=False``, the A2A
default) or a ``ResponseStream`` (``stream=True``). These helpers let each
middleware express "inspect/replace the final text" once and work in both modes.
For streams the check runs on the *final* response; per-chunk text may already
have been yielded, which is why A2A hosting uses non-streaming execution.
"""
from __future__ import annotations

from typing import Any, Callable

from agent_framework import AgentContext, AgentResponse, AgentResponseUpdate, Content, Message, ResponseStream


def message_text(msg: Any) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        return str(msg.get("text") or msg.get("content") or "")
    text = getattr(msg, "text", None)
    return str(text) if text is not None else str(msg)


def input_text(context: AgentContext) -> str:
    """Concatenated text of the inbound messages (user turn + replayed history)."""
    return "\n".join(message_text(m) for m in (context.messages or []))


def last_user_text(context: AgentContext) -> str:
    for m in reversed(context.messages or []):
        if getattr(m, "role", "user") == "user":
            return message_text(m)
    return ""


def response_text(result: Any) -> str:
    if result is None:
        return ""
    return str(getattr(result, "text", "") or "")


def replace_text(response: AgentResponse, new_text: str) -> AgentResponse:
    """Copy of ``response`` whose only content is ``new_text`` (ids and usage preserved)."""
    return AgentResponse(
        messages=[Message("assistant", [new_text])],
        response_id=response.response_id,
        agent_id=response.agent_id,
        usage_details=response.usage_details,
        finish_reason=response.finish_reason,
    )


def short_circuit(context: AgentContext, text: str) -> None:
    """Set a final result without running the agent (used for blocked inputs)."""
    if context.stream:
        async def _gen():
            yield AgentResponseUpdate(role="assistant", contents=[Content.from_text(text)])

        context.result = ResponseStream(_gen(), finalizer=lambda ups: AgentResponse.from_updates(ups))
    else:
        context.result = AgentResponse(messages=[Message("assistant", [text])])


def transform_final_text(context: AgentContext, fn: Callable[[str], str | None]) -> None:
    """Apply ``fn(text) -> new_text | None`` to the run's final text (None = unchanged)."""

    def _apply(resp: AgentResponse) -> AgentResponse:
        out = fn(resp.text or "")
        return resp if out is None else replace_text(resp, out)

    result = context.result
    if isinstance(result, ResponseStream):
        context.result = result.with_result_transform(_apply)
    elif isinstance(result, AgentResponse):
        context.result = _apply(result)
