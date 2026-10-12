"""Optional: run the whole mesh in ONE process under MAF DevUI (development only).

Every node agent is built in-process and the workflow's A2A transport is replaced
by a direct ``agent.run`` call, so DevUI can show the full trace tree
(workflow -> executors -> agents -> tools). MCP-backed agents need the mock
services running (``python -m src.mock_services``); their MCP tools connect
lazily on first use.

    python devui_app.py      # then open http://127.0.0.1:8090
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler  # noqa: E402

from src.agents.node_registry import NODE_NAMES, build_node  # noqa: E402
from src.auth.identity_provider import login  # noqa: E402
from src.config import Config  # noqa: E402
from src.mesh import workflow as wf  # noqa: E402
from src.observability import setup_observability  # noqa: E402
from src.tools import collaboration_tools  # noqa: E402


class DevUIEntryExecutor(Executor):
    """Adapts DevUI's plain-text input into a CaseState for the configured demo user."""

    @handler
    async def run(self, query: str, ctx: WorkflowContext[wf.CaseState]) -> None:
        user = login(Config.DEVUI_USER)
        m = re.search(r"\bCASE-\d{3,}\b", query, re.IGNORECASE)
        await ctx.send_message(wf.CaseState(user_name=user.username, role=user.role, query=query,
                                            case_id=m.group(0).upper() if m else "", session_id="devui"))


def main() -> None:
    try:
        from agent_framework.devui import serve
    except ImportError:
        print("DevUI not installed: pip install --pre agent-framework-devui")
        sys.exit(1)
    setup_observability(service_name="istr_devui")
    agents = {name: build_node(name)[0] for name in NODE_NAMES}

    async def local_ask(name: str, prompt: str, *_a, **_k) -> str:
        res = await agents[name].run(prompt)
        return getattr(res, "text", str(res))

    collaboration_tools.ask_remote = local_ask  # peers resolve in-process too
    entry = DevUIEntryExecutor(id="devui_entry")
    guard = wf.InputGuardrailExecutor(id="input_guardrail")
    rbac = wf.RBACValidationExecutor(id="rbac_validation")
    comp = wf.ComplianceGuardExecutor(local_ask, node=Config.WORKFLOW_COMPLIANCE_NODE, enabled=Config.ENABLE_COMPLIANCE)
    coord = wf.CoordinatorExecutor(local_ask, node=Config.WORKFLOW_COORDINATOR_NODE)
    review = wf.HumanReviewGateExecutor(quality_checks=Config.ENABLE_HUMAN_REVIEW_GATE)
    redact = wf.OutputRedactionExecutor(id="output_redaction")
    workflow = (
        WorkflowBuilder(start_executor=entry, name="istr_investigation_pipeline",
                        output_from=[guard, rbac, comp, redact])
        .add_edge(entry, guard).add_edge(guard, rbac).add_edge(rbac, comp)
        .add_edge(comp, coord).add_edge(coord, review).add_edge(review, redact)
        .build()
    )
    print(f"DevUI on http://{Config.DEVUI_HOST}:{Config.DEVUI_PORT}  user={Config.DEVUI_USER}")
    serve(entities=[workflow, *agents.values()], host=Config.DEVUI_HOST, port=Config.DEVUI_PORT,
          auto_open=False, instrumentation_enabled=True,
          auth_enabled=Config.DEVUI_HOST not in ("127.0.0.1", "localhost"))  # never expose without auth


if __name__ == "__main__":
    main()
