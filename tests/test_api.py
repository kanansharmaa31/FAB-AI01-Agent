"""FastAPI bridge for the UI team: JSON + SSE contracts (A2A transport patched)."""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api_server
from src.config import Config
from src.mesh import orchestrator
from tests.test_workflow import COORD, GUARD, PASS, coordinator_answer, fake_ask

client = TestClient(api_server.app)
ASK = fake_ask({GUARD: PASS, COORD: coordinator_answer()})


@pytest.fixture(autouse=True)
def workflow_nodes(monkeypatch):
    monkeypatch.setattr(Config, "WORKFLOW_COMPLIANCE_NODE", GUARD)
    monkeypatch.setattr(Config, "WORKFLOW_COORDINATOR_NODE", COORD)


def test_health_and_cases():
    assert client.get("/health").json()["status"] in ("ok", "degraded")
    assert [c["case_id"] for c in client.get("/cases").json()] == ["CASE-001", "CASE-002", "CASE-003"]


def test_investigate_returns_draft():
    with patch.object(orchestrator, "ask_remote", ASK):
        body = client.post("/investigate", json={"case_id": "CASE-001", "user": "analyst1"}).json()
    assert body["status"] == "PENDING_MLRO_REVIEW" and body["draft"]["findings"]


def test_investigate_rbac_block_via_api():
    with patch.object(orchestrator, "ask_remote", ASK):
        body = client.post("/investigate", json={"case_id": "CASE-001", "user": "rm1"}).json()
    assert body["blocked"] and body["block_stage"] == "rbac_validation"


def test_investigate_stream_sse_contract():
    with patch.object(orchestrator, "ask_remote", ASK):
        with client.stream("POST", "/investigate/stream", json={"case_id": "CASE-001"}) as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            raw = "".join(resp.iter_text())
    events = []
    for chunk in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    names = [e for e, _ in events]
    assert names[0] == "accepted" and names[-2:] == ["result", "done"]
    assert "stage" in names and "reasoning" in names
    result = dict(events)["result"]
    assert result["status"] == "PENDING_MLRO_REVIEW"
