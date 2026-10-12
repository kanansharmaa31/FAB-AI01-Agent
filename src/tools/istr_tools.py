"""In-process ISTR function tools (``type: function`` ToolRefs).

All tools here are read-only or pure validation. By design there is NO tool that
submits, files, sends or notifies anything (ISTR rule 2 / draft-only); a test
(`tests/test_istr_rules.py`) fails if one is added.

Case lookups read the synthetic case store today; in production they would call
the case-management system, ideally behind its own MCP boundary.
"""
from __future__ import annotations

import json
from typing import Any

from agent_framework import tool
from pydantic import ValidationError

from src.mock_services.data_store import cite, load
from src.schemas.istr import CaseFile, ISTRDraft


def _case(case_id: str) -> dict[str, Any] | None:
    return next((c for c in load("cases")["cases"] if c["case_id"] == case_id.strip().upper()), None)


@tool(description="Return the alert/case record for a case_id (e.g. CASE-001) as a normalised CaseFile with citation.")
def get_case_summary(case_id: str) -> dict[str, Any]:
    c = _case(case_id)
    if not c:
        return {"tool": "get_case_summary", "found": False, "message": f"No case found for case_id={case_id}."}
    case_file = CaseFile.model_validate({**c, "source": cite("get_case_summary", {
        "record_id": c["record_id"], "timestamp": c["alert_date"]})})
    return {"tool": "get_case_summary", "found": True, "case_file": case_file.model_dump(),
            "citations": [case_file.source.model_dump()]}


@tool(description="List the case_ids available to investigate with their alert typology.")
def list_cases() -> dict[str, Any]:
    return {"tool": "list_cases", "cases": [
        {"case_id": c["case_id"], "alert_typology": c["alert_typology"], "customer_id": c["subject"]["customer_id"]}
        for c in load("cases")["cases"]
    ]}


@tool(description=(
    "Validate a draft ISTR JSON object: schema, status=PENDING_MLRO_REVIEW, and that every finding "
    "has at least one citation (tool, record_id, timestamp). Returns a list of issues; empty means valid."
))
def validate_istr_draft(draft_json: str) -> dict[str, Any]:
    try:
        data = json.loads(draft_json)
    except json.JSONDecodeError as exc:
        return {"tool": "validate_istr_draft", "valid": False, "issues": [f"not valid JSON: {exc}"]}
    issues: list[str] = []
    if data.get("status") not in (None, "PENDING_MLRO_REVIEW"):
        issues.append("status must be PENDING_MLRO_REVIEW (drafts are never filed by the mesh)")
        data.pop("status")
    try:
        ISTRDraft.model_validate(data)
    except ValidationError as exc:
        issues.extend(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return {"tool": "validate_istr_draft", "valid": not issues, "issues": issues}
