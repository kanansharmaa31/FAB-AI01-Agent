"""Whole mesh in one process: real factory + stub LLM + real peer tools + workflow.

No sockets: A2A hops go to in-process agents, and each MCP service is replaced
by its mock functions (filtered by the same allow lists). This proves the agent
folders, factory, middleware, peer delegation, reasoning relay and the
human-review gate work together, offline, for all three synthetic cases.

It runs whatever agents exist under src/agents/ with the configured workflow
nodes, so it keeps working as agents are added or removed.
"""
import asyncio
from unittest.mock import patch

import pytest
from agent_framework import tool

from src.agents.node_registry import NODE_NAMES, build_node
from src.auth.identity_provider import login
from src.config import Config
from src.mesh import orchestrator
from src.mock_services.data_store import load
from src.mock_services.server import SERVICE_MODULES
from src.tools import collaboration_tools as ct

MCP_SUBSTITUTES = {s: [tool(f) for f in m.TOOLS] for s, m in SERVICE_MODULES.items()}
EXPECTED = {c["case_id"]: c["expected_outcome"] for c in load("cases")["cases"]}


def _mesh():
    agents = {n: build_node(n, mcp_tools=MCP_SUBSTITUTES)[0] for n in NODE_NAMES}

    async def local_ask(node, prompt, **_):
        return (await agents[node].run(prompt)).text

    return local_ask


@pytest.mark.parametrize("case_id", sorted(EXPECTED))
def test_case_end_to_end(case_id):
    ask = _mesh()
    with patch.object(orchestrator, "ask_remote", ask), patch.object(ct, "ask_remote", ask):
        r = asyncio.run(orchestrator.handle_request(login("analyst1"), case_id=case_id))
    assert not r.blocked, r.summary
    d = r.draft
    assert r.status == d["status"] == "PENDING_MLRO_REVIEW"
    assert d["recommendation"] == EXPECTED[case_id]
    assert d["findings"] and all(f["citations"] for f in d["findings"])
    assert d["missing_evidence"] == [] and d["review_issues"] == []
    assert d["narrative"].startswith("DRAFT NARRATIVE")
    cited = {c["record_id"] for f in d["findings"] for c in f["citations"]}
    assert any(rid.startswith("TXN-") for rid in cited)            # MCP tool reached (second round)
    assert {e["agent"] for e in r.reasoning} >= {Config.WORKFLOW_COORDINATOR_NODE, "example_specialist"}


def test_specialist_down_is_reported_as_missing_evidence():
    ask = _mesh()

    async def flaky(node, prompt, **kw):
        if node == "example_specialist":
            raise ConnectionError("specialist node down")
        return await ask(node, prompt, **kw)

    with patch.object(orchestrator, "ask_remote", flaky), patch.object(ct, "ask_remote", flaky), \
            patch.object(ct, "_A2A_RETRY_DELAY", 0):
        r = asyncio.run(orchestrator.handle_request(login("analyst1"), case_id="CASE-002"))
    assert r.status == "PENDING_MLRO_REVIEW"
    assert any(m.startswith("example_specialist") for m in r.draft["missing_evidence"])
