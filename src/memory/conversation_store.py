"""Conversation memory facade used by the orchestrator.

Replays the last N turns for a ``case_id``/``session_id`` into the coordinator
prompt (the reference's "MAF thread memory + JSONL persistence" pattern). The
remote A2A nodes stay stateless; only the orchestrator reads/writes memory.
"""
from __future__ import annotations

from src.config import Config
from src.memory.base import ConversationBackend


def _build_backend(name: str | None = None) -> ConversationBackend:
    if (name or Config.CONVERSATION_BACKEND).strip().lower() == "redis":
        from src.memory.redis_backend import RedisBackend

        return RedisBackend()
    from src.memory.jsonl_backend import JsonlBackend

    return JsonlBackend()


class ConversationStore:
    def __init__(self, backend: ConversationBackend | None = None) -> None:
        self._backend = backend or _build_backend()

    @staticmethod
    def key(case_id: str | None, session_id: str | None) -> str:
        """Memory is scoped to (case, session): history never crosses cases or analysts' sessions."""
        return f"{case_id or 'nocase'}__{session_id or 'default'}"

    def load(self, key: str, max_turns: int) -> list[dict]:
        msgs = self._backend.load_messages(key)
        return msgs[-(2 * max_turns):] if max_turns > 0 else []

    def load_all(self, key: str) -> list[dict]:
        return self._backend.load_messages(key)

    def append_turn(self, key: str, user_query: str, answer: str) -> None:
        self._backend.append(key, "user", user_query)
        self._backend.append(key, "assistant", answer)

    def clear(self, key: str) -> None:
        self._backend.clear(key)

    @staticmethod
    def format_history_block(messages: list[dict]) -> str:
        if not messages:
            return ""
        lines = ["[Conversation so far]"]
        for m in messages:
            content = (m.get("content") or "").strip()
            if content:
                lines.append(f"{'Analyst' if m.get('role') == 'user' else 'Assistant'}: {content}")
        return "\n".join(lines + ["", "[Current request]", ""])
