"""File-based JSONL backend (default): one append-only file per memory key.

Zero infrastructure, stdlib only. Keys are sanitised to a safe charset to prevent
path traversal. ``data/conversations`` is git-ignored.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re

from src.config import Config
from src.memory.base import ConversationBackend

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


class JsonlBackend(ConversationBackend):
    def __init__(self, store_dir: str | None = None) -> None:
        self._dir = Config.resolve_path(store_dir or Config.CONVERSATION_STORE_DIR)

    def _path(self, key: str) -> pathlib.Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir / f"{_UNSAFE.sub('_', key or 'default')}.jsonl"

    def load_messages(self, key: str) -> list[dict]:
        path = self._path(key)
        if not path.exists():
            return []
        out: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("role") and "content" in rec:
                out.append(rec)
        return out

    def append(self, key: str, role: str, content: str) -> None:
        rec = {"role": role, "content": content, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        with self._path(key).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def clear(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
