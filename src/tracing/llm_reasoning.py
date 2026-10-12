"""Parses ``<llm_reasoning>{json}</llm_reasoning>`` blocks emitted by agents.

Ported from the reference. Agents self-identify with an ``"agent"`` key so blocks
relayed through the coordinator are still attributed to the right specialist.
Malformed JSON is kept as ``{"raw": ...}`` rather than dropped.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

_REASONING_RE = re.compile(r"<llm_reasoning>(.*?)<\s*/\s*llm_reasoning\s*>", re.DOTALL | re.IGNORECASE)


@dataclass
class LLMReasoningEntry:
    agent: str
    phase: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def strip_reasoning_markers(text: str) -> str:
    return _REASONING_RE.sub("", text or "").strip()


def extract_reasoning(text: str, agent: str) -> tuple[list[LLMReasoningEntry], str]:
    """Returns ``(entries, text_without_blocks)``."""
    entries: list[LLMReasoningEntry] = []
    for match in _REASONING_RE.finditer(text or ""):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            data = {"raw": raw}
        if not isinstance(data, dict):
            data = {"raw": data}
        entries.append(LLMReasoningEntry(agent=str(data.get("agent", agent)),
                                         phase=str(data.get("phase", "unknown")), data=data))
    return entries, strip_reasoning_markers(text)
