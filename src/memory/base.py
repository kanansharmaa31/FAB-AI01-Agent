"""Conversation storage backend interface.

History is keyed by a memory key derived from ``case_id`` + ``session_id`` (see
``ConversationStore.key``), so an analyst's follow-up questions on a case resolve
in context, while two analysts or two cases never share history.

Messages are MAF-compatible role/content dicts: ``{"role", "content", "ts"}``.
"""
from __future__ import annotations

import abc


class ConversationBackend(abc.ABC):
    @abc.abstractmethod
    def load_messages(self, key: str) -> list[dict]:
        """All messages for ``key`` in order; skip corrupt records rather than raise."""

    @abc.abstractmethod
    def append(self, key: str, role: str, content: str) -> None:
        """Append one message."""

    def clear(self, key: str) -> None:  # optional
        return None
