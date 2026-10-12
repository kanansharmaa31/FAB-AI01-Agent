"""ToolResolver: function | mcp | a2a_peer, allow-list enforcement, peer safeguards."""
from unittest.mock import patch

import pytest
from agent_framework import FunctionTool, MCPStreamableHTTPTool

from src.factory.spec import AgentSpec
from src.factory.tool_resolver import ToolResolutionError, ToolResolver
from src.integrations.mcp_clients import make_mcp_tool
from src.tools import collaboration_tools as ct


def spec_with(tools, kind="specialist"):
    return AgentSpec.model_validate({"name": "t_agent", "public_name": "TAgent", "description": "resolver test agent",
                                     "owner": "t", "kind": kind, "a2a": {"port": 8998}, "tools": tools})


def test_function_tool_and_plain_callable():
    tools = ToolResolver().resolve(spec_with([
        {"type": "function", "ref": "src.tools.istr_tools:get_case_summary"},  # already a FunctionTool
        {"type": "function", "ref": "src.mock_services.policy_rag:search_policies"},  # plain function -> wrapped
    ]))
    assert all(isinstance(t, FunctionTool) for t in tools)
    assert [t.name for t in tools] == ["get_case_summary", "search_policies"]


def test_function_tool_import_error():
    with pytest.raises(ToolResolutionError, match="cannot import"):
        ToolResolver().resolve(spec_with([{"type": "function", "ref": "src.tools.istr_tools:does_not_exist"}]))


def test_mcp_unconnected_with_allow_list():
    (tool,) = ToolResolver().resolve(spec_with([{"type": "mcp", "service": "screening", "allow": ["screen_customer"]}]))
    assert isinstance(tool, MCPStreamableHTTPTool)
    assert tool.allowed_tools == ["screen_customer"]


def test_mcp_connected_tool_wider_than_spec_is_rejected():
    wide = make_mcp_tool("kyc")  # all kyc tools
    with pytest.raises(ToolResolutionError, match="wider than the spec allow list"):
        ToolResolver().resolve(spec_with([{"type": "mcp", "service": "kyc", "allow": ["get_risk_rating"]}]),
                               mcp_tools={"kyc": wide})


def test_mcp_in_process_substitutes_filtered_to_allow():
    from agent_framework import tool
    from src.mock_services import kyc

    subs = [tool(f) for f in kyc.TOOLS]
    tools = ToolResolver().resolve(spec_with([{"type": "mcp", "service": "kyc", "allow": ["get_risk_rating"]}]),
                                   mcp_tools={"kyc": subs})
    assert [t.name for t in tools] == ["get_risk_rating"]


def test_peer_tool_named_and_described():
    (tool,) = ToolResolver({"screening": "Screens things."}).resolve(
        spec_with([{"type": "a2a_peer", "node": "screening"}]))
    assert tool.name == "consult_screening" and "Screens things." in tool.description


async def test_peer_calls_node_with_depth_header():
    seen = []

    async def fake(node, prompt, **_):
        seen.append((node, prompt))
        return "ok <llm_reasoning>{\"agent\":\"screening\",\"phase\":\"tool_selection\"}</llm_reasoning>"

    entries, token = ct.start_reasoning_collection()
    with patch.object(ct, "ask_remote", fake):
        out = await ct.consult_peer("screening", "check CUST-1001")
        again = await ct.consult_peer("screening", "check CUST-1001")  # cached
    ct.stop_reasoning_collection(token)
    assert out == again == "ok"
    assert len(seen) == 1 and seen[0][1].startswith("[peer_depth=1]")
    assert entries and entries[0]["agent"] == "screening"


async def test_peer_soft_fails_and_depth_guard():
    async def boom(node, prompt, **_):
        raise ConnectionError("refused")

    with patch.object(ct, "ask_remote", boom), patch.object(ct, "_A2A_RETRY_DELAY", 0):
        out = await ct.consult_peer("customer_kyc", "q-soft-fail")
    assert out.startswith("CUSTOMER_KYC_UNAVAILABLE")

    token = ct.set_depth(2)
    try:
        out = await ct.consult_peer("customer_kyc", "q-depth", max_depth=2)
    finally:
        ct.reset_depth(token)
    assert out.startswith("PEER_LIMIT")
