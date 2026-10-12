"""Redis backend (stub, not active by default).

A minimal list-per-key implementation so ``CONVERSATION_BACKEND=redis`` can be
tried, import-guarded so the mesh runs without the ``redis`` package. Before
production use, decide TTL/retention with FAB records-management (ISTR case
history may be subject to retention rules) and enable TLS/auth.
"""
from __future__ import annotations

import datetime
import json

from src.config import Config
from src.memory.base import ConversationBackend

_PREFIX = "istr:conv:"


class RedisBackend(ConversationBackend):
    def __init__(self, url: str | None = None) -> None:
        try:
            import redis  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("CONVERSATION_BACKEND=redis requires 'pip install redis'") from exc
        self._r = redis.Redis.from_url(url or Config.CONVERSATION_REDIS_URL, decode_responses=True)

    def load_messages(self, key: str) -> list[dict]:
        out = []
        for raw in self._r.lrange(_PREFIX + key, 0, -1):
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return out

    def append(self, key: str, role: str, content: str) -> None:
        rec = {"role": role, "content": content, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        self._r.rpush(_PREFIX + key, json.dumps(rec, ensure_ascii=False))

    def clear(self, key: str) -> None:
        self._r.delete(_PREFIX + key)
