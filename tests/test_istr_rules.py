"""ISTR-wide rules enforced as tests (README section 5)."""
import ast
import pathlib

import pytest
from pydantic import ValidationError

from src.factory.loader import SpecLoader
from src.schemas.istr import Finding, ISTRDraft

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN_VERBS = ("submit", "file_", "send", "notify", "email", "transmit", "goaml", "fiu_")


def test_rule1_finding_requires_citation():
    with pytest.raises(ValidationError):
        Finding(finding_id="F-1", title="t", description="d", citations=[])


def test_rule2_draft_status_is_structurally_draft_only():
    assert ISTRDraft(case_id="CASE-001").status == "PENDING_MLRO_REVIEW"
    with pytest.raises(ValidationError):
        ISTRDraft(case_id="CASE-001", status="FILED")
    with pytest.raises(ValidationError):
        ISTRDraft(case_id="CASE-001", recommendation="convict")


def test_rule2_no_outbound_capability_in_codebase():
    """No function in src/ may be named like an outbound filing/notification action."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(node.name.lower().startswith(v) or f"_{v}" in node.name.lower() for v in FORBIDDEN_VERBS):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.name}")
    assert offenders == [], f"outbound-looking functions found: {offenders}"


def test_rules_3_and_4_every_agent_has_tipping_off_audit_and_pii_redaction():
    for name, spec in SpecLoader().specs().items():
        assert "audit" in spec.middleware, name
        assert "tipping_off" in spec.middleware, name
        assert spec.guardrails.pii_redaction, name


def test_rule5_least_privilege_explicit_allow_lists():
    for name, spec in SpecLoader().specs().items():
        for ref in spec.mcp_tools:
            assert ref.allow, name
        if spec.kind == "guardrail":
            assert spec.tools == [], name


def test_shared_rules_injected_into_every_prompt():
    loader = SpecLoader()
    rules = (ROOT / "src" / "agents" / "_shared" / "istr_rules.md").read_text(encoding="utf-8").strip()
    for name, la in loader.load().items():
        assert rules in loader.render_instructions(la.spec, la.prompt_template), name


def test_synthetic_data_is_marked_synthetic():
    for path in (ROOT / "data" / "synthetic").glob("*.json"):
        assert "SYNTHETIC" in path.read_text(encoding="utf-8"), path.name
