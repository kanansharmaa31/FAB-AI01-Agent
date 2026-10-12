"""ISTR domain schemas (pydantic v2).

These types are the contract between agents, the workflow and the UI. Two ISTR
rules are enforced *structurally* here rather than by prompt:

- Evidence-grounded (rule 1): a :class:`Finding` must carry at least one
  :class:`Citation` (tool + record_id + timestamp). No source, no claim.
- Draft-only (rule 2): :attr:`ISTRDraft.status` is a ``Literal`` with a single
  value, ``PENDING_MLRO_REVIEW``. There is no "filed"/"submitted" state to set.

Field lists are a working baseline; the final ISTR report fields are an open
question for FAB (see README).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DRAFT_STATUS = "PENDING_MLRO_REVIEW"

Recommendation = Literal["escalate", "close", "request_info"]
Severity = Literal["low", "medium", "high"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Citation(BaseModel):
    """Where a fact came from. Every finding needs at least one."""

    model_config = ConfigDict(extra="ignore")

    tool: str = Field(..., min_length=1, description="Tool / MCP tool that returned the record")
    record_id: str = Field(..., min_length=1, description="Source record identifier")
    timestamp: str = Field(..., min_length=1, description="Record timestamp (ISO-8601)")


class Subject(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_id: str
    display_name: str = ""
    segment: str = ""
    accounts: list[str] = Field(default_factory=list)


class CaseFile(BaseModel):
    """Normalised alert/case (e.g. produced by a case-intake agent)."""

    model_config = ConfigDict(extra="ignore")

    case_id: str
    alert_id: str
    alert_typology: str
    alert_date: str
    triggering_rule: str = ""
    alert_summary: str = ""
    subject: Subject
    source: Citation | None = None


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    evidence_id: str
    category: str = Field(..., description="transactions | kyc | screening | network | policy | case")
    summary: str
    source: Citation


class Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    finding_id: str
    title: str
    description: str
    typology: str = ""
    severity: Severity = "medium"
    citations: list[Citation] = Field(..., min_length=1)


class ISTRDraft(BaseModel):
    """Draft Internal Suspicious Transaction Report. Always awaits MLRO review."""

    model_config = ConfigDict(extra="ignore")

    case_id: str
    status: Literal["PENDING_MLRO_REVIEW"] = DRAFT_STATUS
    subject: Subject | None = None
    typologies: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    narrative: str = ""
    recommendation: Recommendation = "request_info"
    rationale: str = ""
    missing_evidence: list[str] = Field(default_factory=list)
    review_issues: list[str] = Field(default_factory=list)
    generated_by: str = "coordinator"
    generated_at: str = Field(default_factory=_now)
    disclaimer: str = (
        "AI-generated DRAFT for MLRO review. Not a determination of wrongdoing. "
        "Not filed or shared with any external party."
    )
