"""Mock KYC/CDD service."""
from __future__ import annotations

from typing import Any

from src.mock_services.data_store import cite, load, not_found
from src.mock_services.transactions import _txns

SERVICE = "kyc"


def _profile(customer_id: str) -> dict[str, Any] | None:
    return next((p for p in load("kyc")["profiles"] if p["customer_id"] == customer_id), None)


def get_kyc_profile(customer_id: str) -> dict[str, Any]:
    """Full KYC/CDD profile for a customer."""
    p = _profile(customer_id)
    if not p:
        return not_found("get_kyc_profile", "customer_id", customer_id)
    return {"tool": "get_kyc_profile", "found": True, "profile": p, "citations": [cite("get_kyc_profile", p)]}


def get_expected_activity(customer_id: str) -> dict[str, Any]:
    """Expected-vs-actual activity comparison (credit turnover and cash share)."""
    p = _profile(customer_id)
    if not p:
        return not_found("get_expected_activity", "customer_id", customer_id)
    rows = _txns(customer_id)
    credits = [r for r in rows if r["direction"] == "in"]
    actual_credit = sum(r["amount"] for r in credits)
    cash = sum(r["amount"] for r in credits if r["type"] == "cash_deposit")
    cash_pct = round(100 * cash / actual_credit, 1) if actual_credit else 0.0
    expected = p["expected_monthly_credit_turnover_aed"]
    return {
        "tool": "get_expected_activity",
        "found": True,
        "customer_id": customer_id,
        "expected_monthly_credit_turnover_aed": expected,
        "actual_30d_credit_turnover_aed": actual_credit,
        "turnover_ratio": round(actual_credit / expected, 2) if expected else None,
        "expected_cash_share_pct": p["expected_cash_share_pct"],
        "actual_cash_share_pct": cash_pct,
        "supporting_documents": p.get("supporting_documents", []),
        "citations": [cite("get_expected_activity", p)] + [cite("get_expected_activity", r) for r in credits],
    }


def get_risk_rating(customer_id: str) -> dict[str, Any]:
    """Current customer risk rating, CDD level and PEP flag."""
    p = _profile(customer_id)
    if not p:
        return not_found("get_risk_rating", "customer_id", customer_id)
    return {
        "tool": "get_risk_rating", "found": True, "customer_id": customer_id,
        "risk_rating": p["risk_rating"], "cdd_level": p["cdd_level"], "pep_flag": p["pep_flag"],
        "next_review_due": p["next_review_due"], "citations": [cite("get_risk_rating", p)],
    }


TOOLS = [get_kyc_profile, get_expected_activity, get_risk_rating]
