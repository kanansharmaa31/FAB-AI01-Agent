"""Thin FastAPI bridge for the investigation-workspace UI.

Endpoints:
    GET  /health                       liveness
    GET  /cases                        synthetic sample cases
    GET  /mesh/status                  fan-out /health to every node
    POST /investigate                  run one investigation, return the full result
    POST /investigate/stream           SSE: one event per workflow stage, then the result
    GET  /conversations/{case_id}/{session_id}   replayable history

Auth is a placeholder (``user`` in the body maps to a demo identity); replace with
FAB SSO before any non-local use. The API never files or sends anything.

    uvicorn api_server:app --port 8000     (or: python api_server.py)
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from src.agents.node_registry import NODE_NAMES, workflow_node_problems  # noqa: E402
from src.auth.identity_provider import login  # noqa: E402
from src.config import Config  # noqa: E402
from src.memory import ConversationStore  # noqa: E402
from src.mesh.orchestrator import handle_request, handle_request_stream  # noqa: E402
from src.mock_services.data_store import load  # noqa: E402
from src.observability import setup_observability  # noqa: E402

setup_observability(service_name="istr_api")
app = FastAPI(title="ISTR Agent Mesh API", version="0.1.0",
              description="Draft-only ISTR investigation API (FAB GERNAS AI-01). Synthetic data.")


class InvestigateRequest(BaseModel):
    case_id: str = Field(..., examples=["CASE-001"])
    query: str = ""
    user: str = Field("analyst1", description="Demo identity; replace with SSO")
    session_id: str | None = None


@app.get("/health")
async def health() -> dict:
    problems = workflow_node_problems()
    return {"status": "degraded" if problems else "ok", "provider": Config.LLM_PROVIDER, "nodes": NODE_NAMES,
            "coordinator": Config.WORKFLOW_COORDINATOR_NODE, "compliance": Config.WORKFLOW_COMPLIANCE_NODE or None,
            "problems": problems}


@app.get("/cases")
async def cases() -> list[dict]:
    return [{k: c[k] for k in ("case_id", "alert_id", "alert_typology", "alert_date", "alert_summary")}
            for c in load("cases")["cases"]]


@app.get("/mesh/status")
async def mesh_status() -> dict:
    async def _one(client: httpx.AsyncClient, node: str) -> tuple[str, dict]:
        try:
            r = await client.get(Config.agent_url(node) + "health", timeout=2.0)
            return node, r.json()
        except Exception as exc:
            return node, {"status": "down", "error": str(exc)[:120]}

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(_one(client, n) for n in NODE_NAMES))
    return dict(results)


@app.post("/investigate")
async def investigate(req: InvestigateRequest) -> dict:
    result = await handle_request(login(req.user), req.query, case_id=req.case_id, session_id=req.session_id)
    return result.to_dict()


@app.post("/investigate/stream")
async def investigate_stream(req: InvestigateRequest) -> StreamingResponse:
    queue: asyncio.Queue = asyncio.Queue()
    request_id = uuid.uuid4().hex[:12].upper()
    task = asyncio.create_task(handle_request_stream(login(req.user), req.query, case_id=req.case_id,
                                                     session_id=req.session_id, request_id=request_id,
                                                     event_queue=queue))

    async def _sse():
        yield f"event: accepted\ndata: {json.dumps({'request_id': request_id, 'case_id': req.case_id})}\n\n"
        while (ev := await queue.get()) is not None:
            yield f"event: {ev.get('event_type', 'stage')}\ndata: {json.dumps(ev, default=str)}\n\n"
        try:
            result = await task
            yield f"event: result\ndata: {json.dumps(result.to_dict(), default=str)}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'error': str(exc)[:300]})}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/conversations/{case_id}/{session_id}")
async def conversation(case_id: str, session_id: str) -> dict:
    key = ConversationStore.key(case_id.upper(), session_id)
    return {"key": key, "messages": ConversationStore().load_all(key)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=Config.API_HOST, port=Config.API_PORT, log_level="info")
