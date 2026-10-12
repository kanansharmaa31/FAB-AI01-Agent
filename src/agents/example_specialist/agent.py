"""ExampleSpecialistAgent (`example_specialist`, kind: mcp_thin). REFERENCE AGENT.

Shows the standard structure of a specialist agent. Copy this folder (or run
``scripts/new_agent.py``) to create real agents, then delete it when no longer needed.

Files in this folder:
    agent.py         this module: the Python entry point (and home for agent-only tools)
    agent.spec.yaml  configuration: tools, model profile, port, middleware, guardrails
    prompt.md        instructions (ISTR-wide rules are appended automatically)

The agent is built by the shared AgentFactory from agent.spec.yaml + prompt.md,
so model client, tools, middleware and guardrails are wired the same way for
every agent. Equivalent of the reference repo's ``get_<x>_agent()`` builders.

Agent-only tools are defined below with ``@tool`` and listed in agent.spec.yaml
(``check_alert_age`` is the example). Tools shared by several agents belong in
``src/tools/`` instead.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_framework import Agent, tool

from src.factory.agent_factory import default_factory
from src.mock_services.data_store import cite, load

NAME = "example_specialist"


def get_example_specialist_agent(log_path: str | None = None, mcp_tools: dict[str, Any] | None = None) -> Agent:
    """Builds the ExampleSpecialistAgent.

    Args:
        log_path: optional audit log path (defaults to AUDIT_LOG_FILE).
        mcp_tools: connected MCP tools by service name; a2a_server.py passes these.
            When omitted, MCP tools are created unconnected.
    """
    return default_factory().create(NAME, log_path=log_path, mcp_tools=mcp_tools)


# --- Agent-only tools ---------------------------------------------------------------

@tool(description="Return how many days an alert (by case_id, e.g. CASE-001) has been open, with a citation.")
def check_alert_age(case_id: str) -> dict[str, Any]:
    """Example agent-only tool: read-only, returns `found` and `citations` like every tool should."""
    case = next((c for c in load("cases")["cases"] if c["case_id"] == case_id.strip().upper()), None)
    if case is None:
        return {"tool": "check_alert_age", "found": False, "message": f"No case found for case_id={case_id}."}
    opened = datetime.fromisoformat(case["alert_date"])
    days = (datetime.now(timezone.utc) - opened).days
    return {
        "tool": "check_alert_age", "found": True, "case_id": case["case_id"], "alert_id": case["alert_id"],
        "days_open": days,
        "citations": [cite("check_alert_age", {"record_id": case["record_id"], "timestamp": case["alert_date"]})],
    }
