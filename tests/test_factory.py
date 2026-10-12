"""AgentFactory: builds every spec, wires tools/middleware, resolves models, fails fast."""
import pytest
from agent_framework import Agent, MCPStreamableHTTPTool

from src.factory.agent_factory import AgentFactory
from src.factory.model_clients import ModelConfigError, build_chat_client, resolve_model
from src.factory.spec import AgentSpec
from src.factory.stub_client import StubChatClient
from src.middleware.audit_middleware import AuditMiddleware
from src.middleware.budget_middleware import ToolBudgetRunMiddleware
from src.middleware.guardrail_middleware import GuardrailMiddleware
from src.middleware.peer_middleware import MeshContextMiddleware, ReasoningRelayMiddleware
from src.middleware.tipping_off_middleware import TippingOffMiddleware
from tests.fakes import ScriptedChatClient


def _tools(agent: Agent) -> list:
    return list(agent.default_options.get("tools") or []) + list(getattr(agent, "mcp_tools", []) or [])


def test_builds_every_spec(factory):
    for spec in factory.list_specs():
        agent = factory.create(spec.name)
        assert isinstance(agent, Agent)
        assert agent.name == spec.public_name


def test_build_node_registry_shape(factory):
    agent, public_name, description = factory.build_node("example_specialist")
    assert public_name == "ExampleSpecialistAgent" and "transaction" in description


def test_coordinator_gets_peer_tools_only(factory):
    names = sorted(t.name for t in _tools(factory.create("example_coordinator")))
    assert names == ["consult_example_specialist"]


def test_specialist_gets_all_three_tool_styles(factory):
    tools = _tools(factory.create("example_specialist"))
    names = {getattr(t, "name", "") for t in tools}
    assert {"get_case_summary", "check_alert_age"} <= names            # shared + agent-only function tools
    assert any(isinstance(t, MCPStreamableHTTPTool) for t in tools)     # MCP tool


def test_mcp_tool_is_unconnected_and_least_privilege(factory):
    tools = _tools(factory.create("example_specialist"))
    mcp = [t for t in tools if isinstance(t, MCPStreamableHTTPTool)]
    assert len(mcp) == 1
    assert mcp[0].allowed_tools == ["detect_patterns", "get_counterparties"]
    assert mcp[0].url.endswith(":9101/mcp")


def test_middleware_stack_order(factory):
    stack = factory.create("example_coordinator").agent_middleware
    kinds = [type(m) for m in stack]
    assert kinds[0] is MeshContextMiddleware
    assert kinds.index(AuditMiddleware) < kinds.index(TippingOffMiddleware) < kinds.index(ToolBudgetRunMiddleware)
    assert GuardrailMiddleware in kinds and kinds[-1] is ReasoningRelayMiddleware


def test_create_from_spec_adhoc():
    spec = AgentSpec.model_validate({"name": "adhoc_agent", "public_name": "AdhocAgent",
                                     "description": "Ad-hoc agent built in a test.", "owner": "t",
                                     "kind": "specialist", "a2a": {"port": 8999}})
    f = AgentFactory(chat_client_factory=lambda s: ScriptedChatClient(["hello"]))
    agent = f.create_from_spec(spec, instructions="Be brief.")
    assert agent.name == "AdhocAgent"


async def test_injected_fake_client_runs_without_network(factory):
    f = AgentFactory(chat_client_factory=lambda s: ScriptedChatClient(["COMPLIANCE_PASSED: fine"]))
    res = await f.create("example_coordinator").run("Investigate CASE-001")  # no MCP tools: fully offline
    assert res.text.startswith("COMPLIANCE_PASSED")


# --- model resolution ------------------------------------------------------------

def test_model_resolution_precedence(factory, monkeypatch):
    from src.config import Config

    spec = factory.get_spec("example_specialist")
    monkeypatch.setattr(Config, "LLM_MODEL_DEFAULT", "base-model")
    assert resolve_model(spec).model == "base-model"
    monkeypatch.setattr(Config, "LLM_MODEL_REASONING", "big-model")
    assert resolve_model(factory.get_spec("example_coordinator")).model == "big-model"  # profile: reasoning
    monkeypatch.setenv("EXAMPLE_SPECIALIST_MODEL", "override-model")
    monkeypatch.setenv("EXAMPLE_SPECIALIST_PROVIDER", "openai_compat")
    monkeypatch.setenv("EXAMPLE_SPECIALIST_API_KEY", "sk-test-1234")
    r = resolve_model(spec)
    assert (r.provider, r.model, r.api_key) == ("openai_compat", "override-model", "sk-test-1234")
    assert "***1234" in r.describe()


def test_stub_provider_returns_stub_client(factory):
    assert isinstance(build_chat_client(factory.get_spec("example_specialist")), StubChatClient)


def test_fail_fast_when_key_missing(factory, monkeypatch):
    from src.config import Config

    monkeypatch.setenv("EXAMPLE_SPECIALIST_PROVIDER", "openai_compat")
    monkeypatch.setattr(Config, "LLM_MODEL_DEFAULT", "some-model")
    monkeypatch.setattr(Config, "LLM_API_KEY", "")
    with pytest.raises(ModelConfigError, match="no API key"):
        build_chat_client(factory.get_spec("example_specialist"))


def test_unknown_provider_and_missing_extension(factory, monkeypatch):
    monkeypatch.setenv("EXAMPLE_SPECIALIST_PROVIDER", "carrier_pigeon")
    with pytest.raises(ModelConfigError, match="unknown LLM provider"):
        build_chat_client(factory.get_spec("example_specialist"))
    monkeypatch.setenv("EXAMPLE_SPECIALIST_PROVIDER", "azure_openai")
    with pytest.raises(ModelConfigError, match="AZURE_OPENAI_ENDPOINT"):
        build_chat_client(factory.get_spec("example_specialist"))


def test_openai_compat_client_builds_with_key(factory, monkeypatch):
    from agent_framework.openai import OpenAIChatCompletionClient
    from src.config import Config

    monkeypatch.setenv("EXAMPLE_SPECIALIST_PROVIDER", "openai_compat")
    monkeypatch.setattr(Config, "LLM_MODEL_DEFAULT", "local-model")
    monkeypatch.setattr(Config, "LLM_API_KEY", "not-needed-locally")
    assert isinstance(build_chat_client(factory.get_spec("example_specialist")), OpenAIChatCompletionClient)
