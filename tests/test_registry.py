"""node_registry is derived from specs: names, ports, MCP wiring, start order."""
import pathlib

from src.agents import node_registry as reg

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_registry_matches_spec_directories():
    dirs = {p.name for p in (ROOT / "src" / "agents").iterdir()
            if p.is_dir() and not p.name.startswith("_") and (p / "agent.spec.yaml").exists()}
    assert set(reg.NODE_NAMES) == dirs == set(reg.AGENT_REGISTRY)


def test_ports_unique_and_from_specs():
    assert len(set(reg.NODE_PORTS.values())) == len(reg.NODE_PORTS)
    assert all(reg.NODE_PORTS[n] == s.a2a.port for n, s in reg.SPECS.items())


def test_mcp_backed_nodes_and_services():
    assert reg.MCP_BACKED_NODES == {n for n, s in reg.SPECS.items() if s.mcp_tools}
    assert "example_specialist" in reg.MCP_BACKED_NODES
    assert reg.NODE_MCP_SERVICES["example_specialist"] == ["transactions"]
    assert reg.NODE_MCP_SERVICES["example_coordinator"] == []


def test_start_order_puts_coordinator_last():
    order = reg.start_order()
    coordinators = [n for n in order if reg.SPECS[n].kind == "coordinator"]
    assert order[-len(coordinators):] == coordinators


def test_registry_entries_have_card_metadata():
    builder, public_name, description = reg.AGENT_REGISTRY["example_specialist"]
    assert callable(builder) and public_name == "ExampleSpecialistAgent" and description


def test_workflow_node_wiring_checked(monkeypatch):
    from src.config import Config

    assert reg.workflow_node_problems() == []
    monkeypatch.setattr(Config, "WORKFLOW_COORDINATOR_NODE", "removed_agent")
    monkeypatch.setattr(Config, "WORKFLOW_COMPLIANCE_NODE", "missing_guard")
    problems = " ".join(reg.workflow_node_problems())
    assert "removed_agent" in problems and "missing_guard" in problems


def test_every_agent_folder_has_py_spec_and_prompt():
    for name in reg.NODE_NAMES:
        folder = ROOT / "src" / "agents" / name
        assert {"agent.py", "agent.spec.yaml", "prompt.md"} <= {p.name for p in folder.iterdir()}, name


def test_registry_builders_are_the_agent_py_entry_points():
    import importlib

    for name in reg.NODE_NAMES:
        module = importlib.import_module(f"src.agents.{name}.agent")
        builder = getattr(module, f"get_{name}_agent")
        assert reg.AGENT_REGISTRY[name][0] is builder
        agent, public_name, _ = reg.build_node(name)
        assert agent.name == public_name
