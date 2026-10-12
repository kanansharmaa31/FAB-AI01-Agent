"""Mock transactions service (stands in for the GERNAS SQL Agent).

Pattern checks are deterministic rules over synthetic data, so the same case
always produces the same findings, which the offline suite and smoke test rely on.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.mock_services.data_store import cite, load, not_found

SERVICE = "transactions"

# Synthetic internal review band for cash structuring checks.
_CASH_BAND = (45_000, 55_000)
_STRUCTURING_MIN_COUNT = 3


def _txns(customer_id: str) -> list[dict[str, Any]]:
    rows = [t for t in load("transactions")["transactions"] if t["customer_id"] == customer_id]
    return sorted(rows, key=lambda t: t["timestamp"])


def get_transactions(customer_id: str, max_rows: int = 50) -> dict[str, Any]:
    """Return the customer's transactions (newest last), each with a citation."""
    rows = _txns(customer_id)
    if not rows:
        return not_found("get_transactions", "customer_id", customer_id)
    rows = rows[-max_rows:]
    return {
        "tool": "get_transactions",
        "found": True,
        "customer_id": customer_id,
        "row_count": len(rows),
        "transactions": rows,
        "citations": [cite("get_transactions", r) for r in rows],
    }


def detect_patterns(customer_id: str) -> dict[str, Any]:
    """Run deterministic structuring / round-tripping / high-risk-jurisdiction checks."""
    rows = _txns(customer_id)
    if not rows:
        return not_found("detect_patterns", "customer_id", customer_id)
    high_risk = set(load("transactions")["high_risk_jurisdictions"])
    patterns: list[dict[str, Any]] = []

    cash = [r for r in rows if r["type"] == "cash_deposit" and _CASH_BAND[0] <= r["amount"] < _CASH_BAND[1]]
    if len(cash) >= _STRUCTURING_MIN_COUNT:
        branches = sorted({r["channel"] for r in cash})
        patterns.append({
            "pattern": "structuring",
            "detail": (f"{len(cash)} cash deposits between AED {_CASH_BAND[0]:,} and {_CASH_BAND[1]:,} "
                       f"across {len(branches)} branches, total AED {sum(r['amount'] for r in cash):,}"),
            "citations": [cite("detect_patterns", r) for r in cash],
        })

    by_cp: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        by_cp[r["counterparty"]].add(r["direction"])
    for cp, dirs in by_cp.items():
        if {"in", "out"} <= dirs and cp not in ("Cash",) and not cp.startswith("Own account"):
            linked = [r for r in rows if r["counterparty"] == cp]
            patterns.append({
                "pattern": "round_tripping",
                "detail": f"Funds sent to and received from the same counterparty: {cp}",
                "citations": [cite("detect_patterns", r) for r in linked],
            })

    hr = [r for r in rows if r["country"] in high_risk]
    if hr:
        patterns.append({
            "pattern": "high_risk_jurisdiction",
            "detail": f"{len(hr)} transfer(s) involving a high-risk jurisdiction",
            "citations": [cite("detect_patterns", r) for r in hr],
        })

    large = [r for r in rows if r["amount"] >= 500_000]
    if large:
        patterns.append({
            "pattern": "large_value_movement",
            "detail": f"{len(large)} transaction(s) >= AED 500,000",
            "citations": [cite("detect_patterns", r) for r in large],
        })

    return {"tool": "detect_patterns", "found": True, "customer_id": customer_id,
            "patterns": patterns, "pattern_count": len(patterns)}


def get_counterparties(customer_id: str) -> dict[str, Any]:
    """Aggregate in/out value per counterparty."""
    rows = _txns(customer_id)
    if not rows:
        return not_found("get_counterparties", "customer_id", customer_id)
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = agg.setdefault(r["counterparty"], {"counterparty": r["counterparty"], "country": r["country"],
                                               "in_aed": 0, "out_aed": 0, "record_ids": []})
        a["in_aed" if r["direction"] == "in" else "out_aed"] += r["amount"]
        a["record_ids"].append(r["record_id"])
    return {"tool": "get_counterparties", "found": True, "customer_id": customer_id,
            "counterparties": list(agg.values()),
            "citations": [cite("get_counterparties", r) for r in rows]}


TOOLS = [get_transactions, detect_patterns, get_counterparties]
