"""Mock policy-RAG service (stands in for the GERNAS RAG-as-a-Service MCP).

Keyword scoring over synthetic passages, so retrieval is deterministic. Each
passage returns doc/section/effective_date for citation.
"""
from __future__ import annotations

import re
from typing import Any

from src.mock_services.data_store import cite, load

SERVICE = "policy_rag"

_WORD = re.compile(r"[a-z][a-z\-]+")


def search_policies(query: str, top_k: int = 3) -> dict[str, Any]:
    """Return up to ``top_k`` cited policy passages relevant to ``query``."""
    terms = set(_WORD.findall(query.lower()))
    scored = []
    for p in load("policies")["passages"]:
        kw = {k.lower() for k in p["keywords"]}
        score = sum(1 for k in kw if k in terms or any(k in t or t in k for t in terms if len(t) > 3))
        if score:
            scored.append((score, p))
    scored.sort(key=lambda s: (-s[0], s[1]["record_id"]))
    top = scored[: max(1, min(int(top_k), 8))]
    passages = [dict(p, score=round(min(1.0, s / 4), 2)) for s, p in top]
    return {
        "tool": "search_policies", "query": query, "total_results": len(passages), "passages": passages,
        "citations": [cite("search_policies", p) for p in passages],
    }


TOOLS = [search_policies]
