"""ISTR investigation pipeline as a Microsoft Agent Framework Workflow.

    input_guardrail -> rbac_validation -> compliance_review (A2A) -> investigation (A2A)
                    -> human_review_gate -> output_redaction

Which agents the A2A steps call is configuration, not code:
``WORKFLOW_COORDINATOR_NODE`` (default ``example_coordinator``) runs the
investigation; ``WORKFLOW_COMPLIANCE_NODE`` (default empty = step skipped and
logged) gives the semantic safety verdict. Agents themselves are added or
removed under ``src/agents/``.

A single :class:`CaseState` flows through the graph. Gates either forward
(``ctx.send_message``) or terminate early (``ctx.yield_output``) with
``blocked=True``. A2A executors receive an injected ``ask`` callable so the
offline tests patch the transport at ``orchestrator.ask_remote`` (reference seam).

Draft-only guarantee: ``human_review_gate`` always stamps ``PENDING_MLRO_REVIEW``.
That status is a single-value ``Literal`` on ``ISTRDraft``, and no tool in the
codebase can submit anything, so "filed" is unrepresentable rather than
merely discouraged.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler
from pydantic import ValidationError
from typing_extensions import Never

from src.auth.identity_provider import INVESTIGATION_ROLES
from src.config import Config
from src.guardrails.deterministic_filters import redact_pii, screen_input, screen_output
from src.memory import ConversationStore
from src.mesh import events
from src.observability import CAT_WORKFLOW, get_logger
from src.schemas.istr import DRAFT_STATUS, ISTRDraft
from src.tracing.llm_reasoning import extract_reasoning, strip_reasoning_markers

_log = get_logger(CAT_WORKFLOW)

AskRemote = Callable[..., Awaitable[str]]

_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_UNAVAILABLE = re.compile(r"\b([A-Z][A-Z_]+)_UNAVAILABLE\b")


@dataclass
class CaseState:
    """The single message that flows through the workflow graph."""

    user_name: str
    role: str
    query: str
    case_id: str = ""
    session_id: str = "default"
    request_id: str = ""
    compliance_verdict: str = ""
    investigation: str = ""          # coordinator's raw answer (reasoning stripped)
    summary: str = ""                # analyst-facing summary (no JSON)
    draft: dict[str, Any] | None = None
    status: str = "IN_PROGRESS"
    blocked: bool = False
    block_stage: str | None = None
    trail: list[str] = field(default_factory=list)
    conversation_history: list[dict] = field(default_factory=list)
    reasoning: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)


def _block(state: CaseState, stage: str, message: str, trail: str) -> CaseState:
    state.blocked, state.block_stage, state.summary, state.status = True, stage, message, "BLOCKED"
    state.trail.append(trail)
    events.stage(state, stage, "blocked", message)
    _log.warning("BLOCK stage=%s case=%s: %s", stage, state.case_id, message[:160])
    return state


class InputGuardrailExecutor(Executor):
    """Deterministic input screen (ISTR rule 6: deterministic before semantic)."""

    @handler
    async def run(self, state: CaseState, ctx: WorkflowContext[CaseState, CaseState]) -> None:
        events.stage(state, self.id, "started", "Deterministic input screening")
        res = screen_input(state.query)
        if not res.allowed:
            await ctx.yield_output(_block(state, self.id, f"Request blocked by deterministic guardrails "
                                          f"({', '.join(res.categories)}).", f"guardrail_block:{','.join(res.categories)}"))
            return
        state.trail.append("guardrail_pass")
        events.stage(state, self.id, "completed", "Input passed deterministic checks")
        await ctx.send_message(state)


class RBACValidationExecutor(Executor):
    """Only investigation roles may run ISTR work (front-office roles are a tipping-off risk)."""

    @handler
    async def run(self, state: CaseState, ctx: WorkflowContext[CaseState, CaseState]) -> None:
        events.stage(state, self.id, "started", "Checking role")
        if state.role not in INVESTIGATION_ROLES:
            await ctx.yield_output(_block(state, self.id, f"Access denied: role '{state.role}' may not run ISTR "
                                          "investigations.", f"rbac_block:{state.role}"))
            return
        state.trail.append(f"rbac_pass:{state.role}")
        events.stage(state, self.id, "completed", f"Role '{state.role}' authorised")
        await ctx.send_message(state)


class ComplianceGuardExecutor(Executor):
    """Semantic safety review over A2A by the ``node`` agent. Fails CLOSED if that agent is unreachable.

    With no node configured (or ENABLE_COMPLIANCE=false) the step is skipped and the
    skip is recorded in the trail and stage events; deterministic gates still apply.
    """

    def __init__(self, ask: AskRemote, node: str = "", id: str = "compliance_review", enabled: bool = True) -> None:
        super().__init__(id=id)
        self._ask = ask
        self._node = node
        self._enabled = enabled

    @handler
    async def run(self, state: CaseState, ctx: WorkflowContext[CaseState, CaseState]) -> None:
        if not self._enabled or not self._node:
            why = "disabled" if not self._enabled else "no_node"
            msg = ("Semantic guard disabled (ENABLE_COMPLIANCE=false)" if not self._enabled
                   else "No compliance agent configured (WORKFLOW_COMPLIANCE_NODE empty); deterministic gates only")
            state.compliance_verdict = f"COMPLIANCE_SKIPPED: {msg}"
            state.trail.append(f"compliance_skipped:{why}")
            events.stage(state, self.id, "completed", msg, bypassed=True)
            await ctx.send_message(state)
            return
        events.stage(state, self.id, "started", "Semantic compliance review")
        history = ConversationStore.format_history_block(state.conversation_history)
        try:
            verdict = await self._ask(self._node, f"[case_id={state.case_id}]\n[request_id={state.request_id}]\n"
                                                       f"Review this ISTR mesh request for safety:\n"
                                                       f"{history}{state.query}")
        except Exception as exc:
            await ctx.yield_output(_block(state, self.id, f"Compliance guard unavailable ({exc}); request not "
                                          "processed (fail closed).", "compliance_unavailable"))
            return
        entries, verdict = extract_reasoning(verdict, self._node)
        state.reasoning.extend(e.to_dict() for e in entries)
        events.reasoning(state, [e.to_dict() for e in entries])
        state.compliance_verdict = verdict.strip()
        if "COMPLIANCE_PASSED" not in verdict.upper() or "COMPLIANCE_FAILED" in verdict.upper():
            await ctx.yield_output(_block(state, self.id, "Request blocked by the compliance guard "
                                          f"(semantic review): {verdict.strip()[:200]}", "compliance_failed"))
            return
        state.trail.append("compliance_pass")
        events.stage(state, self.id, "completed", "Compliance review passed")
        await ctx.send_message(state)


class CoordinatorExecutor(Executor):
    """Sends the case to the coordinator agent ``node`` over A2A. Soft-fails into a missing-evidence draft."""

    def __init__(self, ask: AskRemote, node: str, id: str = "investigation") -> None:
        super().__init__(id=id)
        self._ask = ask
        self._node = node

    @handler
    async def run(self, state: CaseState, ctx: WorkflowContext[CaseState]) -> None:
        events.stage(state, self.id, "started", "Coordinator planning and delegating to specialists")
        t0 = time.perf_counter()
        prompt = (
            f"[User: {state.user_name} | Role: {state.role}]\n[case_id={state.case_id}]\n"
            f"[request_id={state.request_id}]\n"
            f"{ConversationStore.format_history_block(state.conversation_history)}{state.query}"
        )
        try:
            answer = await self._ask(self._node, prompt)
            state.trail.append("coordinator_answer")
        except Exception as exc:
            answer = (f"{self._node.upper()}_UNAVAILABLE: the investigation could not be run ({exc}). "
                      "No evidence was gathered.")
            state.trail.append("coordinator_error")
            events.stage(state, self.id, "failed", str(exc)[:200])
        entries, clean = extract_reasoning(answer, self._node)
        state.reasoning.extend(e.to_dict() for e in entries)
        events.reasoning(state, [e.to_dict() for e in entries])
        state.investigation = clean
        if "coordinator_error" not in state.trail:
            events.stage(state, self.id, "completed", "Investigation synthesised",
                         duration_ms=int((time.perf_counter() - t0) * 1000),
                         agents=sorted({e.get("agent", "") for e in state.reasoning} - {""}))
        await ctx.send_message(state)


def _parse_draft(state: CaseState) -> tuple[ISTRDraft, list[str]]:
    """Builds the ISTRDraft from the coordinator's JSON block, forcing draft-only status."""
    issues: list[str] = []
    blocks = _JSON_BLOCK.findall(state.investigation)
    data: dict[str, Any] = {}
    if blocks:
        try:
            data = json.loads(blocks[-1])
        except json.JSONDecodeError as exc:
            issues.append(f"coordinator draft JSON unreadable: {exc}")
    else:
        issues.append("coordinator returned no structured draft; analyst must assemble findings manually")
    if data.get("status") not in (None, DRAFT_STATUS):
        issues.append(f"coordinator proposed status '{data.get('status')}'; overridden to {DRAFT_STATUS}")
    data["status"] = DRAFT_STATUS
    data.setdefault("case_id", state.case_id)
    try:
        draft = ISTRDraft.model_validate(data)
    except ValidationError as exc:
        issues.extend(f"draft schema: {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        # Keep only well-formed findings; never silently drop the whole investigation.
        good = []
        for f in data.get("findings", []) or []:
            try:
                good.append(ISTRDraft.model_validate({"case_id": state.case_id, "findings": [f]}).findings[0])
            except ValidationError:
                issues.append(f"finding dropped (no valid citation): {str(f.get('title', '?'))[:60]}")
        draft = ISTRDraft(case_id=state.case_id, findings=good,
                          narrative=str(data.get("narrative", "")), missing_evidence=list(data.get("missing_evidence", [])))
    return draft, issues


class HumanReviewGateExecutor(Executor):
    """Stamps PENDING_MLRO_REVIEW and runs draft quality checks. Nothing is filed or sent."""

    def __init__(self, id: str = "human_review_gate", quality_checks: bool = True) -> None:
        super().__init__(id=id)
        self._checks = quality_checks

    @handler
    async def run(self, state: CaseState, ctx: WorkflowContext[CaseState]) -> None:
        events.stage(state, self.id, "started", "Preparing draft for MLRO review")
        draft, issues = _parse_draft(state)

        missing = set(draft.missing_evidence)
        for node in _UNAVAILABLE.findall(state.investigation):
            if not any(node.lower() in m.lower() for m in missing):
                missing.add(f"{node.lower()}: unavailable")
        draft.missing_evidence = sorted(missing)

        if self._checks:
            text = "\n".join([state.investigation, draft.narrative, draft.rationale])
            out = screen_output(text)
            issues.extend(out.violations)
            if "tipping_off" in out.categories:
                draft.narrative = "[WITHHELD: tipping-off language detected; rework required]"
            if not draft.findings:
                issues.append("no cited findings: the draft cannot support any recommendation")
            if draft.missing_evidence and draft.recommendation == "close":
                issues.append("recommendation 'close' while evidence is missing; MLRO to confirm")

        draft.review_issues = issues
        state.draft = draft.model_dump()
        state.status = DRAFT_STATUS
        state.summary = _JSON_BLOCK.sub("", state.investigation).strip() or "No investigation summary returned."
        state.trail.append("human_review_gate:pending_mlro_review")
        events.stage(state, self.id, "pending_review", f"Draft ready for MLRO review ({len(draft.findings)} "
                     f"findings, {len(issues)} issue(s))", status_code=DRAFT_STATUS,
                     recommendation=draft.recommendation, missing_evidence=draft.missing_evidence)
        await ctx.send_message(state)


class OutputRedactionExecutor(Executor):
    """Deterministic PII redaction of everything leaving the mesh. Terminal node."""

    @handler
    async def run(self, state: CaseState, ctx: WorkflowContext[Never, CaseState]) -> None:
        state.summary = strip_reasoning_markers(redact_pii(state.summary))
        if state.draft:
            state.draft["narrative"] = redact_pii(state.draft.get("narrative", ""))
            for f in state.draft.get("findings", []):
                f["description"] = redact_pii(f.get("description", ""))
        state.trail.append("output_redacted")
        events.stage(state, self.id, "completed", "Output redacted; response ready")
        _log.info("case=%s complete trail=%s", state.case_id, " -> ".join(state.trail))
        await ctx.yield_output(state)


def build_istr_workflow(ask: AskRemote):
    guardrail = InputGuardrailExecutor(id="input_guardrail")
    rbac = RBACValidationExecutor(id="rbac_validation")
    compliance = ComplianceGuardExecutor(ask, node=Config.WORKFLOW_COMPLIANCE_NODE, enabled=Config.ENABLE_COMPLIANCE)
    coordinator = CoordinatorExecutor(ask, node=Config.WORKFLOW_COORDINATOR_NODE)
    review = HumanReviewGateExecutor(quality_checks=Config.ENABLE_HUMAN_REVIEW_GATE)
    redact = OutputRedactionExecutor(id="output_redaction")
    return (
        WorkflowBuilder(
            start_executor=guardrail,
            name="istr_investigation_pipeline",
            description="guardrail -> RBAC -> compliance_review -> investigation -> human_review_gate -> redact",
            output_from=[guardrail, rbac, compliance, redact],
        )
        .add_edge(guardrail, rbac)
        .add_edge(rbac, compliance)
        .add_edge(compliance, coordinator)
        .add_edge(coordinator, review)
        .add_edge(review, redact)
        .build()
    )
