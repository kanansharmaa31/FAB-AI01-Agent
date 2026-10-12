"""Loads the synthetic dataset shared by the mock MCP services and function tools.

Everything under ``data/synthetic`` is fictitious. Each record carries a
``record_id`` and ``timestamp`` so every tool result can be cited (ISTR rule 1).
"""
from __future__ import annotations

import json
import pathlib
from functools import lru_cache
from typing import Any

from src.config import Config


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, Any]:
    """Loads ``data/synthetic/<name>.json`` once per process."""
    path = pathlib.Path(Config.SYNTHETIC_DATA_DIR) / f"{name}.json"
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def cite(tool: str, record: dict[str, Any]) -> dict[str, str]:
    """Citation block attached to every record a tool returns."""
    return {
        "tool": tool,
        "record_id": str(record.get("record_id", "")),
        "timestamp": str(record.get("timestamp", "")),
    }


def not_found(tool: str, key: str, value: str) -> dict[str, Any]:
    """Uniform empty result: agents are told to report absence, never to invent."""
    return {"tool": tool, "found": False, "message": f"No records found for {key}={value}.", "citations": []}
