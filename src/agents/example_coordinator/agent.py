"""ExampleCoordinatorAgent (`example_coordinator`, kind: coordinator). REFERENCE AGENT.

Shows the structure of a coordinator: its only tools are ``a2a_peer`` entries in
agent.spec.yaml, each of which becomes a ``consult_<node>`` tool that calls that
agent over A2A (depth-guarded, soft-failing, cached per request).

The workflow sends each case to the node named by ``WORKFLOW_COORDINATOR_NODE``
(default ``example_coordinator``). Point that at your real coordinator, or
replace the coordinator step with explicit workflow steps (see the guide).

Files in this folder:
    agent.py         this module: the Python entry point
    agent.spec.yaml  configuration: peers, model profile, port, middleware, guardrails
    prompt.md        instructions (ISTR-wide rules + ISTRDraft schema appended automatically)
"""
from __future__ import annotations

from typing import Any

from agent_framework import Agent

from src.factory.agent_factory import default_factory

NAME = "example_coordinator"


def get_example_coordinator_agent(log_path: str | None = None, mcp_tools: dict[str, Any] | None = None) -> Agent:
    """Builds the ExampleCoordinatorAgent.

    Args:
        log_path: optional audit log path (defaults to AUDIT_LOG_FILE).
        mcp_tools: unused by this agent (no MCP tools); kept for a uniform builder signature.
    """
    return default_factory().create(NAME, log_path=log_path, mcp_tools=mcp_tools)
