"""Mock sanctions / PEP / adverse-media screening service."""
from __future__ import annotations

from typing import Any

from src.mock_services.data_store import cite, load, not_found

SERVICE = "screening"


def screen_customer(customer_id: str) -> dict[str, Any]:
    """All screening hits (sanctions, PEP, adverse media) recorded for a customer."""
    hits = [h for h in load("screening")["hits"] if h["customer_id"] == customer_id]
    if not hits:
        return {"tool": "screen_customer", "found": True, "customer_id": customer_id, "hits": [],
                "summary": "No screening hits.", "citations": []}
    return {
        "tool": "screen_customer", "found": True, "customer_id": customer_id, "hits": hits,
        "open_or_confirmed": [h["record_id"] for h in hits if h["status"] in ("open", "confirmed")],
        "citations": [cite("screen_customer", h) for h in hits],
    }


def screen_name(name: str) -> dict[str, Any]:
    """Screen a free-text name (e.g. a counterparty) against the synthetic lists."""
    needle = name.strip().lower()
    hits = [h for h in load("screening")["hits"] if needle and needle in h["matched_name"].lower()]
    if not hits:
        return not_found("screen_name", "name", name)
    return {"tool": "screen_name", "found": True, "hits": hits,
            "citations": [cite("screen_name", h) for h in hits]}


TOOLS = [screen_customer, screen_name]
