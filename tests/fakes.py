"""Fake chat client for unit tests: replays a script of assistant turns.

Composed with MAF's function-invocation / middleware / telemetry layers (like a
real client), so tool calls in the script are executed by the framework and tool
and agent middleware all run for real.

Script items:
- ``str``                               -> assistant text
- ``("call", name, {args})``            -> one tool call
- ``[("call", ...), ("call", ...)]``    -> parallel tool calls in one turn
The last item repeats once the script is exhausted.
"""
from __future__ import annotations

import itertools
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
)
from agent_framework._middleware import ChatMiddlewareLayer
from agent_framework._tools import FunctionInvocationLayer
from agent_framework.observability import ChatTelemetryLayer

_ids = itertools.count(1)


def _contents(item: Any) -> list[Any]:
    if isinstance(item, str):
        return [item]
    calls = item if isinstance(item, list) else [item]
    return [Content.from_function_call(call_id=f"c{next(_ids)}", name=c[1], arguments=c[2] if len(c) > 2 else {})
            for c in calls]


class ScriptedChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, ChatTelemetryLayer, BaseChatClient):
    OTEL_PROVIDER_NAME = "fake"

    def __init__(self, script: list[Any], **kwargs: Any):
        super().__init__(**kwargs)
        self.script = list(script)
        self.calls: list[list[Message]] = []  # messages seen on each model call
        self.model = "fake"

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.calls.append(list(messages))
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        msg = Message("assistant", _contents(self.script[idx]))
        if not stream:
            async def _r() -> ChatResponse:
                return ChatResponse(messages=[msg], model=self.model)
            return _r()

        async def _g():
            yield ChatResponseUpdate(role="assistant", contents=list(msg.contents), model=self.model)
        return ResponseStream(_g(), finalizer=lambda ups: ChatResponse.from_updates(ups))
