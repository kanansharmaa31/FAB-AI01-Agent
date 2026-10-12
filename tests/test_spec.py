"""AgentSpec schema: good specs parse, bad specs fail loudly."""
import pytest
from pydantic import ValidationError

from src.factory.spec import AgentSpec


def base(**over):
    spec = {
        "name": "demo_agent",
        "public_name": "DemoAgent",
        "description": "A demo agent for unit tests.",
        "owner": "tests",
        "kind": "specialist",
        "a2a": {"port": 8099},
    }
    spec.update(over)
    return spec


def test_minimal_spec_gets_safe_defaults():
    s = AgentSpec.model_validate(base())
    assert s.middleware == ["audit", "tipping_off"]
    assert s.max_tool_calls == 15
    assert s.includes == ["istr_rules"]
    assert s.guardrails.pii_redaction is True
    assert s.model.profile == "default"


def test_all_tool_types_parse():
    s = AgentSpec.model_validate(base(tools=[
        {"type": "function", "ref": "src.tools.istr_tools:get_case_summary"},
        {"type": "mcp", "service": "kyc", "allow": ["get_kyc_profile"]},
        {"type": "a2a_peer", "node": "screening", "max_depth": 2},
    ]))
    assert [t.type for t in s.tools] == ["function", "mcp", "a2a_peer"]
    assert s.is_mcp_backed and len(s.peer_tools) == 1 and len(s.function_tools) == 1


@pytest.mark.parametrize("over, fragment", [
    ({"tool": []}, "Extra inputs"),                                      # typo'd field (extra=forbid)
    ({"name": "Bad-Name"}, "snake_case"),
    ({"public_name": "has space"}, "PascalCase"),
    ({"kind": "wizard"}, "kind"),
    ({"a2a": {"port": 80}}, "greater than or equal"),
    ({"tools": [{"type": "mcp", "service": "kyc", "allow": ["drop_tables"]}]}, "has no tool"),
    ({"tools": [{"type": "mcp", "service": "kyc", "allow": []}]}, "at least 1"),
    ({"tools": [{"type": "mcp", "service": "nosuch", "allow": ["x"]}]}, "service"),
    ({"tools": [{"type": "function", "ref": "not a ref"}]}, "package.module:attribute"),
    ({"tools": [{"type": "sql", "query": "x"}]}, "tag"),
    ({"kind": "mcp_thin"}, "requires at least one tool of type 'mcp'"),
    ({"kind": "coordinator"}, "requires at least one tool of type 'a2a_peer'"),
    ({"kind": "guardrail", "tools": [{"type": "a2a_peer", "node": "x_y"}]}, "must not have tools"),
    ({"tools": [{"type": "a2a_peer", "node": "demo_agent"}]}, "itself"),
    ({"middleware": ["audit", "audit"]}, "duplicate middleware"),
    ({"output_schema": "nodots"}, "dotted path"),
    ({"model": {"temperature": 5}}, "less than or equal"),
    ({"tools": [{"type": "mcp", "service": "kyc", "allow": ["get_kyc_profile"]},
                {"type": "mcp", "service": "kyc", "allow": ["get_risk_rating"]}]}, "merge the allow lists"),
])
def test_bad_specs_rejected(over, fragment):
    with pytest.raises(ValidationError) as exc:
        AgentSpec.model_validate(base(**over))
    assert fragment.lower() in str(exc.value).lower()
