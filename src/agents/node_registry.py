"""Mesh node registry, derived from the agent folders (never hand-edited).

Same public surface as the reference (``AGENT_REGISTRY``, ``MCP_BACKED_NODES``,
``NODE_NAMES``, ``build_node``) so ``a2a_server.py``, ``launch_mesh.py`` and
``devui_app.py`` keep the reference's shape. Like the reference, each registry
entry points at the agent's own Python builder (``get_<name>_agent`` in
``src/agents/<name>/agent.py``). The difference is that the entries are
discovered: adding ``src/agents/<name>/`` is all it takes to register a node.

Specs are loaded and validated at import (fast, offline); chat clients are only
built when a node is actually constructed, so importing the registry never needs
an API key.
"""
from __future__ import annotations

from typing import Any, Callable

from src.factory.agent_factory import default_factory
from src.factory.loader import effective_port
from src.factory.spec import AgentSpec

FACTORY = default_factory()
_LOADED = FACTORY.loader.load()
SPECS: dict[str, AgentSpec] = {name: la.spec for name, la in _LOADED.items()}

# node name -> (builder from agent.py, public name, description)
AGENT_REGISTRY: dict[str, tuple[Callable[..., Any], str, str]] = {
    name: (la.builder, la.spec.public_name, la.spec.description) for name, la in _LOADED.items()
}
MCP_BACKED_NODES: set[str] = {name for name, s in SPECS.items() if s.is_mcp_backed}
NODE_NAMES: list[str] = list(SPECS)
NODE_PORTS: dict[str, int] = {name: effective_port(s) for name, s in SPECS.items()}
# node -> MCP services it needs connected (a2a_server opens these sessions).
NODE_MCP_SERVICES: dict[str, list[str]] = {name: [t.service for t in s.mcp_tools] for name, s in SPECS.items()}


def start_order() -> list[str]:
    """Specialists and guardrails first, coordinators last (they depend on peers)."""
    return sorted(NODE_NAMES, key=lambda n: (SPECS[n].kind == "coordinator", NODE_PORTS[n]))


def workflow_node_problems() -> list[str]:
    """Workflow wiring that points at agents that do not exist (e.g. after removing an agent)."""
    from src.config import Config

    problems = []
    if Config.WORKFLOW_COORDINATOR_NODE not in SPECS:
        problems.append(f"WORKFLOW_COORDINATOR_NODE='{Config.WORKFLOW_COORDINATOR_NODE}' is not an agent "
                        f"in src/agents/ (have: {', '.join(NODE_NAMES)}); investigations will return no evidence")
    if Config.WORKFLOW_COMPLIANCE_NODE and Config.WORKFLOW_COMPLIANCE_NODE not in SPECS:
        problems.append(f"WORKFLOW_COMPLIANCE_NODE='{Config.WORKFLOW_COMPLIANCE_NODE}' is not an agent in "
                        "src/agents/; every request will be blocked (compliance fails closed)")
    return problems


def build_node(name: str, log_path: str | None = None, mcp_tools: dict[str, Any] | None = None):
    """Returns ``(agent, public_name, description)`` by calling the agent's own builder."""
    if name not in AGENT_REGISTRY:
        raise ValueError(f"Unknown agent node '{name}'. Valid: {', '.join(NODE_NAMES)}")
    builder, public_name, description = AGENT_REGISTRY[name]
    return builder(log_path=log_path, mcp_tools=mcp_tools), public_name, description
