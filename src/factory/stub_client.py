"""Deterministic offline chat client (``LLM_PROVIDER=stub``).

Why it exists: the mesh must demonstrably run end to end with no API key: A2A
hops, MCP sessions, middleware, the workflow and draft assembly. The stub is a
real ``BaseChatClient`` composed with MAF's function-invocation, middleware and
telemetry layers (the same layering as ``OpenAIChatCompletionClient``), so MAF
drives the real tool-calling loop; only "the model" is scripted.

Behaviour by agent kind:

- ``guardrail``: keyword verdict ``COMPLIANCE_PASSED`` / ``COMPLIANCE_FAILED``.
- ``coordinator``: consults every peer agent once, then assembles an
  ``ISTRDraft`` JSON from the peers' cited findings. A peer whose answer starts
  with "DRAFT NARRATIVE" supplies the narrative; otherwise the stub restates the
  findings. Unavailable peers are listed as missing evidence (rule 7).
- everyone else: calls each available tool once, in rounds: tools whose
  required arguments can be filled from the request (``case_id``) run first;
  tools that need IDs found in those results (``customer_id``) run next. Then it
  reports findings with the citations the tools returned.

It knows no agent by name, so adding or removing agents needs no change here.

It never invents a fact: every finding it emits is derived from a tool result
and carries that result's citations. It is NOT a quality benchmark for prompts.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
)
from agent_framework._middleware import ChatMiddlewareLayer
from agent_framework._tools import FunctionInvocationLayer
from agent_framework.observability import ChatTelemetryLayer

CASE_RE = re.compile(r"\bCASE-\d{3,}\b", re.IGNORECASE)
CUST_RE = re.compile(r"\bCUST-\d{3,}\b", re.IGNORECASE)
JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

_RISKY = re.compile(
    r"(ignore (all |previous )?instructions|tip(ping)?[- ]?off|inform the (customer|subject)|"
    r"tell the (customer|subject)|submit (it |this )?to (goaml|the fiu)|file (it|the report) with|"
    r"bypass|delete (the )?(audit|records)|reveal (the )?system prompt)",
    re.IGNORECASE,
)

_RED_FLAG_PATTERNS = {"structuring", "round_tripping", "high_risk_jurisdiction"}


class StubChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, ChatTelemetryLayer, BaseChatClient):
    OTEL_PROVIDER_NAME = "stub"

    def __init__(self, node: str = "agent", kind: str = "specialist", structured: bool = False,
                 reasoning_trace: bool = True, **kwargs: Any):
        super().__init__(**kwargs)
        self.node = node
        self.kind = kind
        self.structured = structured
        self.reasoning_trace = reasoning_trace
        self.model = "stub-deterministic"

    # ------------------------------------------------------------------ MAF
    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        msg = self._respond(list(messages), list(options.get("tools") or []))
        if not stream:
            async def _resp() -> ChatResponse:
                return ChatResponse(messages=[msg], model=self.model, finish_reason="stop")
            return _resp()

        async def _gen():
            yield ChatResponseUpdate(role="assistant", contents=list(msg.contents), model=self.model)
        return ResponseStream(_gen(), finalizer=lambda ups: ChatResponse.from_updates(ups))

    # --------------------------------------------------------------- script
    def _respond(self, messages: list[Message], tools: list[Any]) -> Message:
        prompt = next((m.text for m in messages if m.role == "user" and m.text), "")
        results = self._results(messages)
        if self.kind == "guardrail":
            return Message("assistant", [self._verdict(prompt)])
        functions = self._functions(tools)
        if self.kind == "coordinator":
            return self._coordinate(prompt, functions, results)
        pending = [f for f in functions if f.name not in results]
        id_source = "\n".join([prompt, *results.values()])  # IDs discovered so far
        calls = [c for c in (self._call_for(f, id_source, question=prompt) for f in pending) if c is not None]
        if calls:
            return Message("assistant", calls)
        if not results and JSON_BLOCK_RE.search(prompt):
            # Drafting from supplied structured findings (a narrative agent): no new facts.
            return Message("assistant", [_narrative_from(prompt)
                                         + self._reasoning({"phase": "narrative", "source": "supplied_findings"})])
        return Message("assistant", [self._specialist_answer(prompt, results)])

    @staticmethod
    def _functions(tools: list[Any]) -> list[Any]:
        out: list[Any] = []
        for t in tools:
            fns = getattr(t, "functions", None)  # connected MCP tool
            out.extend(fns if isinstance(fns, list) else [t])
        return [f for f in out if getattr(f, "name", None)]

    @staticmethod
    def _results(messages: list[Message]) -> dict[str, str]:
        names: dict[str, str] = {}
        for m in messages:
            for c in m.contents:
                if c.type == "function_call":
                    names[c.call_id] = c.name
        out: dict[str, str] = {}
        for m in messages:
            for c in m.contents:
                if c.type == "function_result":
                    res = c.result
                    if isinstance(res, list):
                        res = " ".join(str(getattr(x, "text", None) or x) for x in res)
                    out[names.get(c.call_id, c.call_id)] = str(res)
        return out

    @staticmethod
    def _ids(prompt: str) -> tuple[str, str]:
        case, cust = CASE_RE.search(prompt), CUST_RE.search(prompt)
        return (case.group(0).upper() if case else ""), (cust.group(0).upper() if cust else "")

    def _call_for(self, fn: Any, id_source: str, question: str) -> Content | None:
        case_id, customer_id = self._ids(id_source)
        try:
            schema = fn.parameters() if callable(getattr(fn, "parameters", None)) else {}
        except Exception:
            schema = {}
        args: dict[str, Any] = {}
        for p in schema.get("required", []):
            if p == "case_id" and case_id:
                args[p] = case_id
            elif p in ("customer_id", "entity_id") and customer_id:
                args[p] = customer_id
            elif p in ("query", "question"):
                args[p] = question
            else:
                return None  # cannot fill a required argument honestly: skip the tool
        return Content.from_function_call(call_id=f"call_{self.node}_{fn.name}", name=fn.name, arguments=args)

    # ---------------------------------------------------------- guardrail
    def _verdict(self, prompt: str) -> str:
        m = _RISKY.search(prompt)
        decision = "FAILED" if m else "PASSED"
        line = (f"COMPLIANCE_FAILED: request asks for a prohibited action ('{m.group(0)}')."
                if m else "COMPLIANCE_PASSED: routine AML investigation request.")
        return line + self._reasoning({"phase": "safety_review", "decision": decision,
                                       "risk_signals": [m.group(0)] if m else []})

    # --------------------------------------------------------- specialist
    def _specialist_answer(self, prompt: str, results: dict[str, str]) -> str:
        if not results:
            return ("No tool evidence was available for this request, so no findings are reported."
                    + self._reasoning({"phase": "tool_selection", "tools_selected": []}))
        findings: list[dict[str, Any]] = []
        missing: list[str] = []
        for tool_name, raw in results.items():
            data = _loads(raw)
            if data is None:
                missing.append(f"{tool_name}: unreadable result")
                continue
            if data.get("found") is False:
                missing.append(f"{tool_name}: {data.get('message', 'no records')}")
                continue
            findings.extend(_findings_from(tool_name, data))
        lines = [f"Findings from {self.node} (draft, evidence-cited):"]
        for f in findings:
            refs = ", ".join(c["record_id"] for c in f["citations"][:6])
            lines.append(f"- {f['title']}: {f['description']} [{refs}]")
        if missing:
            lines.append("Missing / empty evidence: " + "; ".join(missing))
        payload = {"agent": self.node, "findings": findings, "missing_evidence": missing}
        body = "\n".join(lines) + "\n```json\n" + json.dumps(payload) + "\n```"
        return body + self._reasoning({"phase": "tool_selection", "tools_selected": sorted(results)})

    # -------------------------------------------------------- coordinator
    def _coordinate(self, prompt: str, functions: list[Any], results: dict[str, str]) -> Message:
        case_id, customer_id = self._ids(prompt)
        found = "\n".join(results.values())
        if not customer_id:
            m = CUST_RE.search(found)
            customer_id = m.group(0).upper() if m else ""
        typology = _first(r'"(?:alert_)?typology":\s*"([^"]+)"', found)

        due = [f for f in functions if f.name not in results]
        if due and not results:
            question = (f"Investigate {case_id}" + (f" (customer {customer_id})" if customer_id else "")
                        + ". Return cited findings only.")
            calls = [c for c in (self._call_for(f, case_id, question=question) for f in due) if c is not None]
            if calls:
                return Message("assistant", calls)
        return Message("assistant", [self._draft(case_id, customer_id, typology, results)])

    @staticmethod
    def _collect(results: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
        findings: list[dict[str, Any]] = []
        missing: list[str] = []
        for tool_name, text in results.items():
            peer = tool_name.removeprefix("consult_")
            if re.match(r"^[A-Z_]+_UNAVAILABLE", text.strip()) or text.startswith("PEER_LIMIT"):
                missing.append(f"{peer}: {text.strip()[:160]}")
                continue
            for block in JSON_BLOCK_RE.findall(text):
                data = _loads(block) or {}
                findings.extend(f for f in data.get("findings", []) if f.get("citations"))
                missing.extend(f"{peer}: {m}" for m in data.get("missing_evidence", []))
        return findings, missing

    def _draft(self, case_id: str, customer_id: str, typology: str, results: dict[str, str]) -> str:
        findings, missing = self._collect(results)
        for i, f in enumerate(findings, 1):
            f["finding_id"] = f"F-{i}"
        patterns = {f.get("typology") for f in findings if f.get("typology")}
        pep_confirmed = any(f.get("typology") == "pep_exposure" for f in findings)
        if patterns & _RED_FLAG_PATTERNS:
            rec, why = "escalate", "Cited red-flag patterns require further investigation."
        elif pep_confirmed:
            rec, why = "request_info", "Confirmed PEP linkage; source of wealth/funds documentation is outstanding."
        elif findings:
            rec, why = "close", "Activity is explained by documented, verified customer information; no open red flags."
        else:
            rec, why = "request_info", "Insufficient evidence was retrieved to support a recommendation."
        drafted = [JSON_BLOCK_RE.sub("", r).strip() for r in results.values()
                   if r.strip().startswith("DRAFT NARRATIVE")]
        narrative = drafted[0] if drafted else _narrative_from(
            "```json\n" + json.dumps({"findings": findings}) + "\n```")
        draft = {
            "case_id": case_id,
            "status": "PENDING_MLRO_REVIEW",
            "subject": {"customer_id": customer_id},
            "typologies": sorted({typology, *patterns} - {"", None}),
            "findings": findings,
            "narrative": narrative,
            "recommendation": rec,
            "rationale": why,
            "missing_evidence": missing,
            "generated_by": self.node,
        }
        summary = (f"Investigation summary for {case_id} ({customer_id}): {len(findings)} cited findings; "
                   f"recommendation for MLRO: {rec}. {why}")
        if missing:
            summary += f" Missing evidence: {len(missing)} source(s) unavailable."
        return (summary + "\n```json\n" + json.dumps(draft) + "\n```"
                + self._reasoning({"phase": "synthesis", "recommendation": rec,
                                   "peers_consulted": sorted(results), "missing": len(missing)}))

    # ------------------------------------------------------------- helpers
    def _reasoning(self, data: dict[str, Any]) -> str:
        if not self.reasoning_trace:
            return ""
        return "\n<llm_reasoning>" + json.dumps({"agent": self.node, **data,
                                                  "rationale": "deterministic stub"}) + "</llm_reasoning>"


def _loads(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _first(pattern: str, text: str) -> str:
    m = re.search(pattern, text or "")
    return m.group(1) if m else ""


def _finding(title: str, description: str, citations: list[dict], typology: str = "",
             severity: str = "medium") -> dict[str, Any]:
    return {"title": title, "description": description, "typology": typology, "severity": severity,
            "citations": citations[:10]}


def _findings_from(tool: str, d: dict[str, Any]) -> list[dict[str, Any]]:
    """Maps a tool result shape to cited findings. Only restates what the tool returned."""
    cits = d.get("citations") or []
    if "days_open" in d:
        return [_finding("Alert age", f"{d['alert_id']} open for {d['days_open']} days", cits, "", "low")]
    if "case_file" in d:
        cf = d["case_file"]
        return [_finding("Alert", f"{cf['alert_id']} ({cf['alert_typology']}) for {cf['subject']['customer_id']}: "
                         f"{cf['alert_summary']}", cits, cf["alert_typology"], "medium")]
    if "patterns" in d:
        return [_finding(f"Pattern: {p['pattern']}", p["detail"], p["citations"], p["pattern"],
                         "high" if p["pattern"] in _RED_FLAG_PATTERNS else "medium")
                for p in d["patterns"] if p.get("citations")]
    if "profile" in d:
        p = d["profile"]
        return [_finding("KYC profile", f"risk_rating={p['risk_rating']}, cdd={p['cdd_level']}, "
                         f"pep_flag={p['pep_flag']}, source_of_funds={p['source_of_funds']}", cits, "", "low")]
    if "turnover_ratio" in d:
        docs = d.get("supporting_documents") or []
        return [_finding("Expected vs actual activity",
                         f"actual credits AED {d['actual_30d_credit_turnover_aed']:,} vs expected monthly "
                         f"AED {d['expected_monthly_credit_turnover_aed']:,} (ratio {d['turnover_ratio']}); "
                         f"cash share {d['actual_cash_share_pct']}% vs expected {d['expected_cash_share_pct']}%"
                         + (f"; supporting documents on file: {', '.join(docs)}" if docs else ""),
                         cits[:4], "", "medium")]
    if "hits" in d:
        out = []
        for h, c in zip(d["hits"], cits):
            typ = "pep_exposure" if h["list_type"].startswith("pep") and h["status"] == "confirmed" else ""
            out.append(_finding(f"Screening {h['list_type']} ({h['status']})",
                                f"{h['matched_name']} score {h['match_score']}: {h['detail']}", [c], typ,
                                "high" if typ else ("low" if h["status"] == "discounted" else "medium")))
        return out
    if "edges" in d or "links" in d:
        edges = d.get("edges") or d.get("links") or []
        rel = "; ".join(f"{e['source']} {e['relation']} {e['target']}" for e in edges[:6])
        return [_finding("Network links", rel or "no links", cits, "", "medium")] if edges else []
    if "passages" in d:
        return [_finding(f"Policy {p['doc_id']} s.{p['section']}", p["text"], [c], "", "low")
                for p, c in zip(d["passages"], cits)]
    if "counterparties" in d:
        top = sorted(d["counterparties"], key=lambda c: -(c["in_aed"] + c["out_aed"]))[:3]
        desc = "; ".join(f"{c['counterparty']} in AED {c['in_aed']:,} / out AED {c['out_aed']:,}" for c in top)
        return [_finding("Top counterparties", desc, cits[:6], "", "low")]
    if "transactions" in d:
        return [_finding("Transaction history", f"{d['row_count']} transactions reviewed", cits[:10], "", "low")]
    if "valid" in d:
        return []
    return []


def _narrative_from(prompt: str) -> str:
    """Narrative drafter: restates the supplied findings only, each with its record ids."""
    m = JSON_BLOCK_RE.search(prompt)
    findings = (_loads(m.group(1)) if m else None) or []
    if isinstance(findings, dict):
        findings = findings.get("findings", [])
    if not findings:
        return "No structured findings were supplied, so no narrative can be drafted without inventing facts."
    sentences = [f"{f['title']}: {f['description']} [{', '.join(c['record_id'] for c in f['citations'][:4])}]."
                 for f in findings]
    return ("DRAFT NARRATIVE (for MLRO review; describes grounds for suspicion, not a finding of guilt). "
            + " ".join(sentences))
