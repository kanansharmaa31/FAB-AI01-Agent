# ISTR Agent Mesh: Base Code & Extension Guide

**Audience:** engineers on the FAB GERNAS AI-01 programme who will build the ISTR agents on top of this codebase.
**Scope:** what the base code already provides, and step-by-step recipes for adding and removing agents and for adding tools, MCP services, middleware, guardrails, model providers and workflow stages.
**Status:** base codebase on synthetic data. It ships with **two reference agents only** (`example_specialist`, `example_coordinator`). They show the structure; the real ISTR agents are still to be decided and added.

---

## Contents

1. [Summary](#1-summary)
2. [Architecture and request flow](#2-architecture-and-request-flow)
3. [What is already in the base code](#3-what-is-already-in-the-base-code)
4. [The reference agents](#4-the-reference-agents)
5. [Anatomy of an agent folder](#5-anatomy-of-an-agent-folder)
6. [Add an agent](#6-add-an-agent)
7. [Remove an agent](#7-remove-an-agent)
8. [Add tools](#8-add-tools)
9. [Other extension points](#9-other-extension-points)
10. [Configuration, running and testing](#10-configuration-running-and-testing)
11. [ISTR rules and where they are enforced](#11-istr-rules-and-where-they-are-enforced)
12. [Real vs mocked, and open questions](#12-real-vs-mocked-and-open-questions)
13. [Checklists](#13-checklists)

---

## 1. Summary

The mesh investigates an AML alert/case and produces a **draft** Internal Suspicious Transaction Report (ISTR) for MLRO review. It is built on **Microsoft Agent Framework (MAF, Python)** and follows the conventions of the reference `agent-mesh` repo:

- Each agent runs as its own **A2A** server process on its own port.
- Data comes from **MCP** services. These are synthetic mocks today, and real GERNAS services later.
- The request pipeline is a MAF **`WorkflowBuilder`** graph.

What this base adds compared with the reference:

- **One folder per agent:** `src/agents/<name>/` holds `agent.py` (the Python entry point, `get_<name>_agent()`), `agent.spec.yaml` (configuration) and `prompt.md` (instructions).
- **One `AgentFactory`** builds every agent from its folder, so model client, tools, middleware, guardrails and instructions are wired identically for every agent.
- **A scaffolder** (`scripts/new_agent.py`) generates a new agent folder and validates it against the rest of the mesh.
- **The registry, ports and MCP wiring are discovered from the agent folders.** Adding or removing an agent needs no registry edits.
- **The workflow's agents are configuration:** `WORKFLOW_COORDINATOR_NODE` and `WORKFLOW_COMPLIANCE_NODE`.
- **The ISTR rules are enforced in code,** not only in prompts. Examples: draft-only status, mandatory citations and tipping-off blocking.

Hard guarantees:

- The output status is always `PENDING_MLRO_REVIEW`.
- No code path files, submits, sends or notifies anything.

---

## 2. Architecture and request flow

```
 UI / CLI ──► api_server.py / run.py ──► orchestrator (MAF Workflow, runs in the caller's process)
                                              │
 input_guardrail ─► rbac_validation ─► compliance_review ─► investigation ─► human_review_gate ─► output_redaction
   (regex)            (roles)           (A2A: WORKFLOW_     (A2A: WORKFLOW_      (draft-only)          (PII)
                                         COMPLIANCE_NODE,    COORDINATOR_NODE)
                                         optional)                 │
                                                                   │ consult_<peer> tools (A2A)
                                                                   ▼
                                                         specialist agents ──MCP──► data services
                                                     (today: example_specialist)    (mock: transactions, kyc,
                                                                                     screening, graph, policy_rag)
```

### Request flow, step by step

| # | Stage (executor id)   | What happens                                                                                                                                                                                                       | On failure                                                                       |
| - | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------- |
| 1 | `input_guardrail`   | Deterministic regex checks for prompt injection, destructive intent and PII                                                                                                                                        | Blocked. No LLM is called.                                                       |
| 2 | `rbac_validation`   | Role must be one of the investigation roles (analyst, investigator, MLRO, compliance, admin)                                                                                                                       | Blocked. Relationship managers are always refused.                               |
| 3 | `compliance_review` | If`WORKFLOW_COMPLIANCE_NODE` is set, an A2A call to that agent for `COMPLIANCE_PASSED` / `COMPLIANCE_FAILED`. If it is empty, the step is **skipped and logged** (`compliance_skipped:no_node`).     | Blocked. If the configured agent is unreachable, the step**fails closed**. |
| 4 | `investigation`     | An A2A call to`WORKFLOW_COORDINATOR_NODE`. The coordinator delegates to its peer agents and returns a summary plus an `ISTRDraft` JSON block.                                                                  | If the coordinator is down, it becomes`missing_evidence` (soft fail).          |
| 5 | `human_review_gate` | Parses and validates the draft and**forces** `PENDING_MLRO_REVIEW`. Drops uncited findings, collects `*_UNAVAILABLE` markers into `missing_evidence`, and flags tipping-off, guilt or filing language. | Never blocks. Writes problems into`review_issues`.                             |
| 6 | `output_redaction`  | PII redaction of the summary, narrative and findings                                                                                                                                                               | —                                                                               |

Throughout the run, the workflow emits stage and reasoning events (`src/mesh/events.py`). These go to the SSE stream (`POST /investigate/stream`) and are also returned in `result.events`.

### Processes at runtime

| Process                                               | Port(s)                         | Started by                                                         |
| ----------------------------------------------------- | ------------------------------- | ------------------------------------------------------------------ |
| Mock MCP services (5 in one process)                  | 9101–9105                      | `python -m src.mock_services` or `launch_mesh.py --with-mocks` |
| One A2A server per agent folder                       | from each spec (8020, 8021, …) | `a2a_server.py --agent <name>` or `launch_mesh.py`             |
| API bridge (optional)                                 | 8000                            | `api_server.py`                                                  |
| DevUI, everything in one process (optional, dev only) | 8090                            | `devui_app.py`                                                   |

---

## 3. What is already in the base code

### 3.1 Entry points

| File                      | Purpose                                                                                                                                                                                                                                                                       |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `a2a_server.py`         | Hosts**one** agent as an A2A server (`--agent <name>`). For agents with MCP tools it opens and keeps alive every MCP session, with backoff retries at startup and reconnects on mid-session drops. If the LLM configuration is missing it fails fast with a fix hint. |
| `launch_mesh.py`        | Starts every agent folder in order (coordinators last).`--with-mocks` starts the mock services first; `--only a,b` starts a subset. Warns if the workflow points at an agent that doesn't exist.                                                                          |
| `run.py`                | CLI client:`python run.py --case CASE-001 --stream` (live stages) or `--json`.                                                                                                                                                                                            |
| `api_server.py`         | FastAPI bridge for the UI team:`/health` (reports workflow wiring problems), `/cases`, `/mesh/status`, `POST /investigate`, `POST /investigate/stream` (SSE), `/conversations/{case}/{session}`.                                                                  |
| `devui_app.py`          | Runs the whole mesh in one process under MAF DevUI for trace inspection (development only).                                                                                                                                                                                   |
| `scripts/new_agent.py`  | Agent scaffolder (see[section 6](#6-add-an-agent)).                                                                                                                                                                                                                            |
| `scripts/smoke_test.py` | Real multi-process run: mocks plus every agent, three sample cases, with assertions. No API key needed.                                                                                                                                                                       |

### 3.2 `src/agents/`: one folder per agent

```
src/agents/
├── node_registry.py          discovers the agent folders (never edited by hand)
├── _shared/                  istr_rules.md (appended to every prompt), reasoning_trace.md
├── _template/                agent.py.tmpl, agent.spec.yaml.tmpl, prompt.md.tmpl, test_agent.py.tmpl
├── example_specialist/       REFERENCE: agent.py + agent.spec.yaml + prompt.md
└── example_coordinator/      REFERENCE: agent.py + agent.spec.yaml + prompt.md
```

Any folder under `src/agents/` that contains an `agent.spec.yaml` is an agent. Folders starting with `_` are ignored.

### 3.3 `src/factory/`: Agent Creation Template + Agent Factory

| Module                     | Responsibility                                                                                                                                                                                                                                                                                                                                                                       |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `spec.py`                | Pydantic v2 models:`AgentSpec`, `ModelProfile`, `FunctionToolRef`, `McpToolRef`, `A2APeerToolRef`, `GuardrailSpec`, `MemorySpec`, `A2ASpec`, `ObservabilitySpec`. All use `extra="forbid"`, so a typo is an error. Includes rules per kind and validation of `allow` against the MCP catalog.                                                                  |
| `loader.py`              | `SpecLoader`: discovers the agent folders, imports each `agent.py` and checks it defines `get_<name>_agent`, and runs cross-agent validation (port clashes, unknown peers, function refs that won't import, missing prompt sections, unresolved variables, unknown middleware/guardrails/includes). Reports **all** errors at once. Also renders the final instructions. |
| `agent_factory.py`       | `AgentFactory` with `create`, `build_node`, `create_from_spec`, `list_specs`, `get_spec`, `instructions_for`. `default_factory()` is the shared instance every `agent.py` uses. Accepts `chat_client_factory=` for tests.                                                                                                                                        |
| `model_clients.py`       | Provider → MAF chat client (`openai_compat`, `azure_openai`, `foundry`, `anthropic`, `ollama`, `stub`). Resolves the model, provider and key per agent, and fails fast with `ModelConfigError`.                                                                                                                                                                       |
| `stub_client.py`         | A deterministic offline "LLM" that MAF's real tool-calling loop drives. It knows no agent by name, so it works with whatever agents exist.                                                                                                                                                                                                                                           |
| `tool_resolver.py`       | ToolRef → MAF tool. Function refs are imported, MCP refs become`MCPStreamableHTTPTool(allowed_tools=allow)`, and A2A peers become `consult_<node>` tools.                                                                                                                                                                                                                       |
| `middleware_registry.py` | Middleware by name (`audit`, `tipping_off`, `pii_redaction`, `token_budget`) plus the auto-added middleware. Builds the ordered stack.                                                                                                                                                                                                                                       |

### 3.4 Other packages

| Package                                     | Contents                                                                                                                                                                                                                                      |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/agents/node_registry.py`             | `AGENT_REGISTRY` (name → `get_<name>_agent` from `agent.py`, public name, description), `MCP_BACKED_NODES`, `NODE_NAMES`, `NODE_PORTS`, `NODE_MCP_SERVICES`, `start_order()`, `build_node()`, `workflow_node_problems()` |
| `src/mesh/`                               | `workflow.py` (`CaseState` and the 6 executors), `orchestrator.py` (`handle_request`, `handle_request_stream`, memory, baggage) and `events.py` (stage/reasoning events, SSE queue)                                               |
| `src/a2a/`                                | `hosting.py` (A2A server, `/health`, trace-context middleware) and `clients.py` (`ask_remote`, the single transport seam that tests patch)                                                                                            |
| `src/integrations/`                       | `mcp_catalog.py` (the tool contract per MCP service) and `mcp_clients.py` (`make_mcp_tool(service, allow)`)                                                                                                                             |
| `src/tools/`                              | `istr_tools.py` (shared read-only tools: `get_case_summary`, `list_cases`, `validate_istr_draft`) and `collaboration_tools.py` (the A2A peer-tool factory with depth guard, soft fail, per-request cache and reasoning relay)       |
| `src/middleware/`                         | Audit (JSONL per agent run and tool call), tipping-off, guardrails/PII, tool-call circuit breaker, mesh context and reasoning relay                                                                                                           |
| `src/guardrails/deterministic_filters.py` | Named checks:`prompt_injection`, `destructive_intent`, `pii`, `tipping_off`, `guilt_language`, `filing_action`. The output checks are negation-aware.                                                                             |
| `src/memory/`                             | `ConversationStore` (scoped by case and session), `JsonlBackend` (the default) and `RedisBackend` (a stub)                                                                                                                              |
| `src/observability/`                      | Logging with trace/case correlation, OTel setup (`OBS_PROFILE=dev\|prod\|off`), baggage and an optional Opik adapter                                                                                                                          |
| `src/mock_services/`                      | Synthetic MCP services:`transactions`, `kyc`, `screening`, `graph`, `policy_rag`                                                                                                                                                    |
| `src/schemas/istr.py`                     | `Citation`, `Subject`, `CaseFile`, `EvidenceItem`, `Finding` (needs at least one citation) and `ISTRDraft` (its status is a single-value `Literal`)                                                                             |
| `src/auth/identity_provider.py`           | Demo users and`ISTRRole`. `INVESTIGATION_ROLES` controls the RBAC gate.                                                                                                                                                                   |
| `src/config.py`                           | `Config`: environment-driven settings with per-agent overrides                                                                                                                                                                              |

### 3.5 Data and tests

- **`data/synthetic/`:** three synthetic cases (`CASE-001` structuring, `CASE-002` PEP-linked, `CASE-003` clean false positive) plus transactions, KYC, screening, graph and policy records. Every record has a `record_id` and `timestamp` so it can be cited.
- **`tests/`:** 132 offline tests, about 15 seconds, with no network and no LLM:
  - `test_spec`, `test_loader`, `test_scaffolder`, `test_factory`, `test_registry`
  - `test_tool_resolver`, `test_middleware`, `test_workflow`, `test_memory`, `test_guardrails`
  - `test_mock_services`, `test_istr_rules`, `test_api`
  - `test_end_to_end_inprocess`, which runs whatever agents exist
  - `tests/agents/`, which holds per-agent tests generated by the scaffolder

---

## 4. The reference agents

These two agents exist **only to show the structure** and keep the pipeline runnable end to end. Replace or delete them once the real agents exist.

| Agent                   | Kind        | Port | Shows                                                                                                                                                                                                                                                                                                          |
| ----------------------- | ----------- | ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `example_coordinator` | coordinator | 8020 | Delegating over A2A (`a2a_peer` tools), `output_schema: ISTRDraft`, memory enabled. The workflow calls it by default (`WORKFLOW_COORDINATOR_NODE`).                                                                                                                                                      |
| `example_specialist`  | mcp_thin    | 8021 | The three tool styles: a**shared** function tool (`get_case_summary` from `src/tools/`), an **agent-only** tool defined in its `agent.py` (`check_alert_age`), and **MCP** tools (`transactions`: `detect_patterns`, `get_counterparties`). Plus the specialist output format. |

**Specialist output contract.** Every specialist ends its answer with a fenced ```json block shaped like `{"agent", "findings": [{"title", "description", "typology", "severity", "citations": [{"tool", "record_id", "timestamp"}]}], "missing_evidence": []}`, followed by a `<llm_reasoning>` block. Coordinators rely on this shape to merge findings.

## 5. Anatomy of an agent folder

```
src/agents/<name>/
├── agent.py          Python entry point
├── agent.spec.yaml   configuration
└── prompt.md         instructions
```

### 5.1 `agent.py`: the Python entry point

```python
NAME = "example_specialist"

def get_example_specialist_agent(log_path=None, mcp_tools=None) -> Agent:
    return default_factory().create(NAME, log_path=log_path, mcp_tools=mcp_tools)

@tool(description="...")             # optional agent-only tools
def check_alert_age(case_id: str) -> dict: ...
```

- `get_<name>_agent` is **required**: the loader rejects the folder without it. It is the equivalent of the reference repo's `get_rag_agent()`, and the registry and `a2a_server.py` call it.
- Agent-only tools live here. They are **not** exposed until `agent.spec.yaml` references them (`src.agents.<name>.agent:my_tool`), so the spec stays the single list of an agent's tools.

### 5.2 `agent.spec.yaml`: configuration

```yaml
name: example_specialist           # snake_case; must equal the folder name; env prefix EXAMPLE_SPECIALIST_*
public_name: ExampleSpecialistAgent
description: >-                    # ≥10 chars; also the consult_<name> tool description coordinators see
  ...
version: 0.1.0
owner: gernas-ai01-team
kind: mcp_thin                     # specialist | mcp_thin | coordinator | guardrail

instructions_file: prompt.md
prompt_vars: {}                    # extra {{ var }} values for the prompt
includes: [istr_rules]             # src/agents/_shared/<name>.md appended to the prompt

model:
  profile: default                 # default | reasoning | fast  -> LLM_MODEL_<PROFILE>
  temperature: 0.0                 # optional: provider, model, max_tokens

tools:
  - {type: function, ref: "src.tools.istr_tools:get_case_summary"}                 # shared tool
  - {type: function, ref: "src.agents.example_specialist.agent:check_alert_age"}   # agent-only tool
  - {type: mcp, service: transactions, allow: [detect_patterns, get_counterparties]}
  # - {type: a2a_peer, node: other_agent, max_depth: 1}                            # another agent as a tool

middleware: [audit, tipping_off, token_budget]   # ordered; first = outermost
max_tool_calls: 8                                # circuit breaker (MAF hard cap = +5)
guardrails: {input: [prompt_injection], output: [tipping_off, guilt_language, filing_action], pii_redaction: true}
memory: {enabled: false, backend: jsonl, max_turns: 3}
output_schema: null                # e.g. src.schemas.istr.ISTRDraft -> JSON schema appended to the prompt
reasoning_trace: true              # append the <llm_reasoning> contract
a2a:
  port: 8021                       # PORT_EXAMPLE_SPECIALIST overrides
  skills: [{id: ..., name: ..., description: ...}]
observability: {span_name: istr.example_specialist, tags: [istr, example]}
```

Rules for each kind (checked in `spec.py`):

| Kind            | Must                                                       | Must not                                        |
| --------------- | ---------------------------------------------------------- | ----------------------------------------------- |
| `mcp_thin`    | have at least one`mcp` tool                              | —                                              |
| `coordinator` | have at least one`a2a_peer` tool                         | be the peer of another agent                    |
| `guardrail`   | —                                                         | have any tools                                  |
| any             | list each MCP service only once (merge the`allow` lists) | list itself as a peer; repeat a middleware name |

### 5.3 `prompt.md`: instructions

- These headings are mandatory: `## ROLE`, `## SCOPE`, `## TOOLS`, `## OPERATING RULES`, `## EVIDENCE & CITATION RULES`, `## REFUSAL/ESCALATION`, `## OUTPUT FORMAT`, `## REASONING TRANSPARENCY`.
- Variables: `{{ name }}`, `{{ public_name }}`, `{{ description }}`, `{{ tool_list }}` (generated from the spec), `{{ max_tool_calls }}`, plus anything in `prompt_vars`. An unresolved `{{ x }}` is a load error.
- Do **not** paste the ISTR rules or the reasoning-block format. The loader appends them.

To see the full instructions an agent receives:

```powershell
.venv\Scripts\python -c "from src.agents.node_registry import FACTORY; print(FACTORY.instructions_for('example_specialist'))"
```

### 5.4 Middleware stack built by the factory

The stack runs in this order, with the first entry outermost:

```
MeshContextMiddleware      (always)   restores [peer_depth], [case_id], [request_id] from inbound A2A prompts
<spec.middleware ...>      in order   e.g. audit (+ tool audit), tipping_off, token_budget (+ function counter)
GuardrailMiddleware        (if guardrails.input/output non-empty)
PIIRedactionMiddleware     (if guardrails.pii_redaction and not already listed)
ReasoningRelayMiddleware   (if the agent has a2a_peer tools)
```

---

## 6. Add an agent

### 6.1 Steps

**1. Scaffold.** This validates against the whole mesh before writing anything:

```powershell
.venv\Scripts\python scripts\new_agent.py transaction_analysis --kind mcp_thin `
    --tools "mcp:transactions[get_transactions|detect_patterns|get_counterparties]" `
    --description "Analyses the subject's transactions for structuring, velocity and round-tripping." `
    --dry-run      # preview; run again without --dry-run to write
```

This creates:

- `src/agents/transaction_analysis/agent.py`
- `src/agents/transaction_analysis/agent.spec.yaml`
- `src/agents/transaction_analysis/prompt.md`
- `tests/agents/test_transaction_analysis.py`

If validation fails, nothing is written and the command exits with code 2.

| Option                                                           | Meaning                                                                                                         |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `name`                                                         | snake_case agent name (= folder name)                                                                           |
| `--kind`                                                       | `specialist` (default), `mcp_thin`, `coordinator`, `guardrail`                                          |
| `--tools`                                                      | Comma-separated:`mcp:<service>`, `mcp:<service>[a\|b]`, `a2a_peer:<agent>`, `function:<pkg.module:attr>` |
| `--port`                                                       | A2A port. Default: the next free one.                                                                           |
| `--description`, `--public-name`, `--owner`, `--profile` | Spec fields                                                                                                     |
| `--dry-run` / `--force`                                      | Preview without writing / overwrite an existing agent                                                           |

**2. Write the prompt.** Replace every `TODO` in `prompt.md` (ROLE, SCOPE, TOOLS, OPERATING RULES). For specialists, keep the specialist OUTPUT FORMAT (findings JSON with citations).

**3. Review the spec:**

- Trim the `allow` lists to the minimum.
- Set `max_tool_calls`.
- Add `guilt_language` to `guardrails.output` if the agent writes conclusions.
- Choose `model.profile`.
- Write a clear `description`; coordinators see it as the tool description.

**4. Connect it to the investigation.** An agent does nothing until something calls it. Pick one:

| The agent is…                                                                 | Do this                                                                                                                                                                      |
| ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A specialist the coordinator should consult                                    | Add`- {type: a2a_peer, node: transaction_analysis, max_depth: 1}` to the coordinator's `agent.spec.yaml`, and describe when to use it in the coordinator's `prompt.md` |
| The new coordinator                                                            | Set`WORKFLOW_COORDINATOR_NODE=<name>` in `.env`                                                                                                                          |
| A semantic safety guard (`--kind guardrail`)                                 | Set`WORKFLOW_COMPLIANCE_NODE=<name>` in `.env`. Its answer must start with `COMPLIANCE_PASSED:` or `COMPLIANCE_FAILED:`.                                             |
| A fixed step in the pipeline (e.g. an approval or a step that must always run) | Add a workflow step ([section 9.5](#95-add-a-workflow-step))                                                                                                                  |

**5. Test and run:**

```powershell
.venv\Scripts\python -m pytest tests\agents\test_transaction_analysis.py   # xfail reminder until the TODOs are filled
.venv\Scripts\python -m pytest
.venv\Scripts\python launch_mesh.py --with-mocks                          # the new agent starts automatically
.venv\Scripts\python run.py --case CASE-001 --stream
.venv\Scripts\python scripts\smoke_test.py --all
```

### 6.2 What updates automatically

You do **not** edit any of these. They're derived from the folders:

- the registry (`AGENT_REGISTRY`, `NODE_NAMES`, `NODE_PORTS`, `MCP_BACKED_NODES`);
- the launcher's start order;
- `a2a_server.py --agent` choices;
- MCP session wiring;
- the A2A agent card;
- the DevUI entity list;
- the per-agent env overrides (`<NAME>_MODEL`, `PORT_<NAME>`, …).

### 6.3 Example: build a coordinator-led investigation team

```powershell
.venv\Scripts\python scripts\new_agent.py customer_kyc --kind mcp_thin --tools "mcp:kyc"
.venv\Scripts\python scripts\new_agent.py screening    --kind mcp_thin --tools "mcp:screening[screen_customer]"
.venv\Scripts\python scripts\new_agent.py istr_coordinator --kind coordinator --profile reasoning `
    --tools "a2a_peer:customer_kyc,a2a_peer:screening,a2a_peer:example_specialist"
```

Then:

1. Fill in the three prompts.
2. In `istr_coordinator/agent.spec.yaml`, add `output_schema: src.schemas.istr.ISTRDraft`, enable `memory`, and add `guilt_language` to the output guardrails. Copy the coordinator prompt sections from `example_coordinator/prompt.md`.
3. Set `WORKFLOW_COORDINATOR_NODE=istr_coordinator`.

### 6.4 Manual alternative

1. Copy `src/agents/example_specialist/` (or `example_coordinator/`) to `src/agents/<name>/`.
2. In `agent.spec.yaml`, set `name: <name>`, a new `public_name` and a unique `a2a.port`.
3. In `agent.py`, set `NAME = "<name>"` and rename the builder to `get_<name>_agent`.
4. Rewrite the prompt and tools.
5. Run `pytest`. The loader reports anything inconsistent.

---

## 7. Remove an agent

### 7.1 Steps

**1. Check who uses it:**

```powershell
.venv\Scripts\python -c "from src.agents.node_registry import SPECS; n='example_specialist'; print([a for a,s in SPECS.items() if any(p.node==n for p in s.peer_tools)])"
```

Also check `.env` for `WORKFLOW_COORDINATOR_NODE` and `WORKFLOW_COMPLIANCE_NODE`.

**2. Disconnect it:**

- Remove its `- {type: a2a_peer, node: <name>}` line from every agent that lists it, and the matching text in those agents' `prompt.md`.
- If it is `WORKFLOW_COORDINATOR_NODE`, point that at another coordinator first.
- If it is `WORKFLOW_COMPLIANCE_NODE`, set that to another guard, or to empty (which skips the semantic step).

**3. Delete its folder and test:**

```powershell
Remove-Item -Recurse src\agents\<name>
Remove-Item tests\agents\test_<name>.py -ErrorAction SilentlyContinue
```

**4. Delete its agent-only tools**, if no other spec references them (they lived in its `agent.py`, so they're gone with the folder). Leave shared tools in `src/tools/` alone if other agents use them.

**5. Remove leftover configuration** in `.env`: `<NAME>_MODEL`, `<NAME>_PROVIDER`, `<NAME>_API_KEY`, `PORT_<NAME>`.

**6. Run** `pytest` and `scripts\smoke_test.py --all`.

### 7.2 What protects you if you forget a step

| Forgotten step                           | What happens                                                                                                                                                                                                                                 |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Another agent still lists it as a peer   | The mesh refuses to start:`a2a_peer node '<name>' does not exist` (loader error, reported with the file path)                                                                                                                              |
| It is still`WORKFLOW_COORDINATOR_NODE` | `launch_mesh.py` prints a WARNING, `/health` reports `degraded`, and investigations return a draft with `<NAME>_UNAVAILABLE` in `missing_evidence`                                                                                 |
| It is still`WORKFLOW_COMPLIANCE_NODE`  | Warning plus`degraded`. Every request is **blocked** (the guard fails closed).                                                                                                                                                       |
| Its generated test still exists          | The test fails (the folder is gone). Delete the test.                                                                                                                                                                                        |
| Tests that name the reference agents     | If you delete`example_specialist` / `example_coordinator`, update the tests that use them as fixtures (`test_factory`, `test_loader`, `test_registry`, `test_scaffolder`, `test_end_to_end_inprocess`) to use your real agents |

### 7.3 Temporarily switching an agent off without deleting it

- **A peer:** remove its `a2a_peer` line from the coordinator, and don't start it (`launch_mesh.py --only ...`).
- **The semantic guard:** `WORKFLOW_COMPLIANCE_NODE=` (empty) or `ENABLE_COMPLIANCE=false`.
- **The coordinator:** point `WORKFLOW_COORDINATOR_NODE` elsewhere.

---

## 8. Add tools

| Use                                                          | Tool type    | Where the code goes                             |
| ------------------------------------------------------------ | ------------ | ----------------------------------------------- |
| Logic used by**one** agent                             | `function` | That agent's`agent.py`                        |
| Logic shared by**several** agents                      | `function` | `src/tools/` (e.g. `istr_tools.py`)         |
| Data owned by a service (SQL Agent, KG, RAG, KYC, screening) | `mcp`      | The MCP service (mock in`src/mock_services/`) |
| Another agent's reasoning                                    | `a2a_peer` | No code: one line in the spec                   |

All tools must be **read-only**. `tests/test_istr_rules.py` fails if any function in `src/` is named like an outbound action: `submit*`, `send*`, `notify*`, `file_*`, `email*`, `transmit*`, `*goaml*`, `*fiu_*`.

### 8.1 Function tool

```python
from agent_framework import tool
from src.mock_services.data_store import cite, load

@tool(description="Return prior alerts for a customer_id, newest first, with citations.")
def get_prior_alerts(customer_id: str) -> dict:
    rows = [c for c in load("cases")["cases"] if c["subject"]["customer_id"] == customer_id]
    if not rows:
        return {"tool": "get_prior_alerts", "found": False, "message": f"No prior alerts for {customer_id}."}
    return {"tool": "get_prior_alerts", "found": True, "alerts": rows,
            "citations": [cite("get_prior_alerts", {"record_id": r["record_id"], "timestamp": r["alert_date"]})
                          for r in rows]}
```

1. Put it in the agent's `agent.py` (agent-only) or in `src/tools/` (shared).
2. Reference it in the spec: `- {type: function, ref: "src.agents.<agent>.agent:get_prior_alerts"}` or `"src.tools.istr_tools:get_prior_alerts"`.
3. Describe it in the agent's `## TOOLS` prompt section, and unit-test it by calling it directly.

Conventions:

- Write an LLM-facing description.
- Use typed parameters.
- Return `found: False` with a message instead of raising.
- Always return `citations: [{tool, record_id, timestamp}]`.

If the data belongs to a FAB system, prefer an MCP tool so it stays behind the MCP boundary.

### 8.2 A tool on an existing MCP service

1. Add a plain function to the mock (e.g. `src/mock_services/transactions.py`) that returns `citations`, and append it to `TOOLS`. The docstring becomes the tool description.
2. Add the name to `SERVICE_CATALOG[...]` in `src/integrations/mcp_catalog.py`, in the same order as `TOOLS` (a test checks this).
3. Add it to the `allow` list of the agents that need it.
4. Describe it in their prompts. Test the mock function in `tests/test_mock_services.py`.

### 8.3 A new MCP service

| # | File                                | Change                                                                                          |
| - | ----------------------------------- | ----------------------------------------------------------------------------------------------- |
| 1 | `src/integrations/mcp_catalog.py` | Add the tool names to`SERVICE_CATALOG` and a line to `SERVICE_DESCRIPTIONS`                 |
| 2 | `src/config.py`                   | Add`"<svc>": os.getenv("<SVC>_MCP_URL", "http://127.0.0.1:91xx/mcp")` to `MCP_SERVICE_URLS` |
| 3 | `src/factory/spec.py`             | Add`"<svc>"` to the `McpService` `Literal`                                                |
| 4 | `src/mock_services/<svc>.py`      | `SERVICE = "<svc>"`, plain functions, `TOOLS = [...]`                                       |
| 5 | `src/mock_services/server.py`     | Import the module and add it to`SERVICE_MODULES`                                              |
| 6 | `.env.example`                    | `<SVC>_MCP_URL=...`                                                                           |
| 7 | Agent spec(s)                       | `- {type: mcp, service: <svc>, allow: [...]}`                                                 |
| 8 | Synthetic data (if needed)          | `data/synthetic/<svc>.json`, marked `SYNTHETIC`                                             |

No changes are needed to `a2a_server.py`, the registry or the launcher.

### 8.4 Swap a mock for the real GERNAS service

1. Set `<SERVICE>_MCP_URL` in `.env` to the real endpoint, plus `MCP_API_KEY` if needed (sent as `X-API-Key`). The mock launcher skips non-local URLs.
2. Align `SERVICE_CATALOG[service]` with the real tool names, then update the `allow` lists and prompts.
3. Make sure results carry citations `{tool, record_id, timestamp}`, or map the real fields to citations in the agent's prompt.
4. Run the agent alone (`a2a_server.py --agent <name>`). It logs `MCP connected`, or retries with backoff.

### 8.5 An A2A peer tool

```yaml
tools:
  - {type: a2a_peer, node: example_specialist, max_depth: 1, description: "Optional override."}
```

This gives you a `consult_<node>(question)` tool with:

- a depth guard that also works across processes;
- soft fail (`<NODE>_UNAVAILABLE` becomes missing evidence);
- a per-request cache;
- reasoning relay;
- case and request IDs carried for the audit trail.

The peer must exist and must not be a coordinator.

---

## 9. Other extension points

### 9.1 Middleware

1. Implement it in `src/middleware/`. Use `transform_final_text(context, fn)` to post-process output (it handles streaming too), and `short_circuit(context, text)` to refuse without calling the LLM.
2. Register it in `MIDDLEWARE_BUILDERS` (`src/factory/middleware_registry.py`).
3. Add its name to a spec's `middleware:` list.
4. Test it in `tests/test_middleware.py` with `ScriptedChatClient`.

### 9.2 Deterministic guardrail check

1. Write `detect_x(text) -> list[str]` in `src/guardrails/deterministic_filters.py`. For output checks, use `_search_unnegated`, so phrases like "do not …" don't trigger it.
2. Register it in `CHECKS`.
3. Use it in specs (`guardrails.input/output`), or workflow-wide via `DEFAULT_INPUT_CHECKS` / `DEFAULT_OUTPUT_CHECKS`.
4. Add positive and negative cases to `tests/test_guardrails.py`.

### 9.3 Models and providers

- **Per agent:** `<NAME>_PROVIDER`, `<NAME>_MODEL`, `<NAME>_API_KEY` in `.env`, or `model.provider` / `model.model` in the spec.
- **By profile:** `LLM_MODEL_DEFAULT`, `LLM_MODEL_REASONING` and `LLM_MODEL_FAST`.
- **New provider type:**
  1. Add it to `PROVIDERS` in `model_clients.py` and to the `Provider` `Literal` in `spec.py`.
  2. Add a branch in `build_chat_client()`. Use `_optional` for lazy imports and `_construct` to tolerate signature differences.
  3. Raise `ModelConfigError` with a fix hint, and add a test.

### 9.4 Draft schema

Edit `src/schemas/istr.py`. Keep `status: Literal["PENDING_MLRO_REVIEW"]` and `Finding.citations` with `min_length=1`. Coordinators with `output_schema` pick up the change automatically.

If you add required fields, also update:

- the stub coordinator's `_draft()` in `stub_client.py`;
- the fixture `coordinator_answer()` in `tests/test_workflow.py`;
- the UI team, who receive the draft as `result.draft`.

### 9.5 Add a workflow step

Use this for fixed pipeline steps that must always run in order, such as a plan approval, a parallel branch, or an MLRO approval gate.

```python
class AgentStepExecutor(Executor):
    def __init__(self, ask, node: str, id: str | None = None):
        super().__init__(id=id or node); self._ask, self._node = ask, node

    @handler
    async def run(self, state: CaseState, ctx: WorkflowContext[CaseState]) -> None:
        events.stage(state, self.id, "started", f"{self._node} working")
        try:
            state.investigation += "\n" + await self._ask(self._node, f"[case_id={state.case_id}]\n...")
        except Exception as exc:
            state.investigation += f"\n{self._node.upper()}_UNAVAILABLE: {exc}"   # fail soft
        events.stage(state, self.id, "completed", f"{self._node} done")
        await ctx.send_message(state)
```

Wire it in `build_istr_workflow()` with `.add_edge(...)`, and mirror the change in `devui_app.py`.

The installed MAF version also provides:

- `add_fan_out_edges` / `add_fan_in_edges` for parallel steps;
- `ctx.request_info(...)` with a `@response_handler`, plus checkpoint storage, for human approval gates that pause and resume.

Add tests in `tests/test_workflow.py`, and tell the UI team the new `stage` name.

### 9.6 Roles

Edit `ISTRRole` and `INVESTIGATION_ROLES` in `src/auth/identity_provider.py`. `login()` is the single place to plug in FAB SSO/Entra ID.

---

## 10. Configuration, running and testing

### 10.1 Setup

```powershell
cd istr-agent-mesh
uv venv .venv --python 3.13            # or: py -3.13 -m venv .venv
uv pip install --python .venv\Scripts\python.exe --prerelease=allow -r requirements.txt
copy .env.example .env                 # LLM_PROVIDER=stub by default
```

`mcp` must stay below version 2 (`mcp[cli]>=1.28,<2`). MAF's MCP client requires it.

### 10.2 Key environment variables

| Variable                                                                             | Default                                        | Purpose                                                                               |
| ------------------------------------------------------------------------------------ | ---------------------------------------------- | ------------------------------------------------------------------------------------- |
| `LLM_PROVIDER`                                                                     | `openai_compat` (`.env.example`: `stub`) | `openai_compat`, `azure_openai`, `foundry`, `anthropic`, `ollama`, `stub` |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_DEFAULT/_REASONING/_FAST`            |                                                | Default endpoint, key and models per profile                                          |
| `<NAME>_PROVIDER` / `_MODEL` / `_API_KEY`, `PORT_<NAME>`                     |                                                | Per-agent overrides                                                                   |
| `WORKFLOW_COORDINATOR_NODE`                                                        | `example_coordinator`                        | Agent that runs the investigation                                                     |
| `WORKFLOW_COMPLIANCE_NODE`                                                         | *(empty)*                                    | Agent for the semantic safety verdict. Empty = step skipped and logged.               |
| `<SERVICE>_MCP_URL`, `MCP_API_KEY`                                               | local mocks 9101–9105                         | Where each MCP service lives                                                          |
| `ENABLE_COMPLIANCE`                                                                | `true`                                       | `false` skips the semantic step even if a node is set                               |
| `ENABLE_HUMAN_REVIEW_GATE`                                                         | `true`                                       | Draft quality checks.**The status stays `PENDING_MLRO_REVIEW` regardless.**   |
| `ENABLE_SENSITIVE_DATA`                                                            | `false`                                      | Keep`false`: audit and traces are redacted                                          |
| `ENABLE_CONVERSATION_MEMORY`, `CONVERSATION_MAX_TURNS`, `CONVERSATION_BACKEND` | `true`, `3`, `jsonl`                     | Memory per case and session                                                           |
| `AUDIT_LOG_FILE`                                                                   | `data/logs/audit_trail.jsonl`                | JSONL audit trail                                                                     |
| `OBS_PROFILE`, `OPIK_*`                                                          | `off`                                        | Observability (OTel, optional Opik)                                                   |

### 10.3 Run

```powershell
.venv\Scripts\python launch_mesh.py --with-mocks          # mocks + every agent folder
.venv\Scripts\python run.py --case CASE-001 --stream      # CLI with live stage events
.venv\Scripts\python run.py --case CASE-001 --user rm1    # see the RBAC block
.venv\Scripts\python api_server.py                        # http://127.0.0.1:8000/docs
```

### 10.4 Test

```powershell
.venv\Scripts\python -m pytest                       # 132 offline tests
.venv\Scripts\python scripts\smoke_test.py --all     # real processes over A2A + MCP, 3 cases
```

| Need                             | Pattern                                                                                                           |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Build an agent with no LLM       | `AgentFactory(chat_client_factory=lambda spec: ScriptedChatClient([...]))`, or the `factory` fixture          |
| Script tool calls                | `ScriptedChatClient([("call", "tool_name", {...}), "final text"])`                                              |
| Fake the A2A transport           | `patch.object(orchestrator, "ask_remote", fake)` and `patch.object(collaboration_tools, "ask_remote", fake)`  |
| Point the workflow at test nodes | `monkeypatch.setattr(Config, "WORKFLOW_COORDINATOR_NODE", "...")` (see `tests/test_workflow.py`)              |
| Whole mesh in-process            | `tests/test_end_to_end_inprocess.py` (MCP replaced by `mcp_tools={service: [tool(f) for f in module.TOOLS]}`) |
| Mutate agent folders safely      | The`agents_copy` fixture (a temporary copy of `src/agents/`)                                                  |

### 10.5 Troubleshooting

| Symptom                                                       | Look at                                                                                                       |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `Agent spec validation failed`                              | The listed files and fields. Every error is shown at once.                                                    |
| Node exits at startup                                         | Its console output.`ModelConfigError` names the missing variable.                                           |
| `MCP connect failed (n/8)`                                  | Are the mocks running? Is the`*_MCP_URL` right?                                                             |
| WARNING about`WORKFLOW_*_NODE` / `/health` = `degraded` | The workflow points at an agent that doesn't exist ([section 7.2](#72-what-protects-you-if-you-forget-a-step)) |
| Draft has`missing_evidence`                                 | A peer returned`<NODE>_UNAVAILABLE`. Check that node's process.                                             |
| What an agent or tool did                                     | `data/logs/audit_trail.jsonl` (one record per agent run and tool call, with `case_id` and `request_id`) |

---

## 11. ISTR rules and where they are enforced

| Rule                             | Enforcement                                                                                                                                                                    | Test                                                        |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------- |
| 1. Evidence-grounded             | `Finding.citations` has `min_length=1`. The review gate drops uncited findings. Tools return `citations`. The rule is in every prompt.                                   | `test_istr_rules`, `test_workflow`                      |
| 2. Draft only, human decides     | `ISTRDraft.status` is a `Literal` forced by the gate. There are no outbound tools (checked by an AST scan). `guilt_language` and `filing_action` checks run on output. | `test_istr_rules`, `test_workflow`                      |
| 3. No tipping-off                | `tipping_off` middleware on every agent, gate checks, RBAC excluding relationship managers, and the optional semantic guard                                                  | `test_middleware`, `test_guardrails`, `test_workflow` |
| 4. PII minimisation and audit    | Redacted, truncated audit records. JSONL audit for every run and tool call. Output redaction stage.                                                                            | `test_middleware`, `test_guardrails`                    |
| 5. Least privilege               | Agents get only their spec's tools. MCP`allow` is required and catalog-checked; MAF `allowed_tools` filters what the model sees. Guardrail agents have no tools.           | `test_spec`, `test_tool_resolver`, `test_istr_rules`  |
| 6. Deterministic before semantic | Workflow order (regex, then RBAC, then the semantic guard) plus agent input guardrails                                                                                         | `test_workflow`                                           |
| 7. Fail soft, not silent         | `<NODE>_UNAVAILABLE` from peers becomes `missing_evidence`. The configured guard fails **closed**. A skipped guard is recorded in the trail.                         | `test_workflow`, `test_end_to_end_inprocess`            |

`tests/test_istr_rules.py` checks every agent folder automatically. For example, every spec must keep `audit` and `tipping_off`, and keep `pii_redaction` on. New agents are covered without writing any new tests.

---

## 12. Real vs mocked, and open questions

| Component                                                    | Status                                                                    |
| ------------------------------------------------------------ | ------------------------------------------------------------------------- |
| MAF agents, middleware, tool loop,`WorkflowBuilder`        | Real                                                                      |
| A2A server and client, MCP transport and`allowed_tools`    | Real (localhost)                                                          |
| Factory, loader, scaffolder, guardrails, audit, JSONL memory | Real                                                                      |
| Agents                                                       | **Two reference agents only.** The ISTR agent set is to be decided. |
| LLM                                                          | `stub` by default; real providers are pluggable                         |
| Business data                                                | Synthetic mocks (`data/synthetic/`, `src/mock_services/`)             |
| Identity / RBAC                                              | Demo users                                                                |
| Redis memory                                                 | Minimal stub                                                              |
| OTel export / Opik                                           | Optional                                                                  |

**Open questions for FAB and the programme:**

1. **The ISTR agent set**, for example a planner, customer context, transaction, screener, RFI, risk/typology, consolidator and narrative. Also: which steps are fixed workflow steps or human gates rather than coordinator-chosen.
2. Final ISTR report fields and the MLRO template.
3. FAB's typologies, thresholds and high-risk jurisdiction list.
4. The approved LLM provider and models, and data residency.
5. The real MCP interfaces of the GERNAS SQL Agent, KG and RAG services (tool names, citation fields).
6. Roles allowed to investigate and approve, and the SSO integration.
7. Retention for memory and audit logs.

---

## 13. Checklists

### Add an agent

- [ ] `new_agent.py --dry-run`, then the real run (creates `agent.py`, `agent.spec.yaml`, `prompt.md`, test)
- [ ] Every `TODO` in `prompt.md` replaced; all 8 sections kept
- [ ] `allow` lists minimal; `max_tool_calls` set; `description` written for the coordinator's LLM
- [ ] Specialist output follows the findings JSON contract with citations
- [ ] Connected: listed as a peer by a coordinator, or set as `WORKFLOW_*_NODE`, or added as a workflow step
- [ ] `pytest` and `scripts\smoke_test.py --all` green

### Remove an agent

- [ ] Removed from every other agent's `a2a_peer` list and prompt
- [ ] Not referenced by `WORKFLOW_COORDINATOR_NODE` / `WORKFLOW_COMPLIANCE_NODE`
- [ ] Folder `src/agents/<name>/` and `tests/agents/test_<name>.py` deleted
- [ ] `<NAME>_*` and `PORT_<NAME>` removed from `.env`
- [ ] `pytest` and `scripts\smoke_test.py --all` green; no WARNING from `launch_mesh.py`

### Add a tool

- [ ] Read-only; not named like an outbound action
- [ ] LLM-facing description; typed parameters; `found: False` on no data; `citations` returned
- [ ] Agent-only tools in `agent.py`, shared tools in `src/tools/`; referenced in the spec
- [ ] MCP: in `TOOLS`, in `SERVICE_CATALOG` (same order), in the agents' `allow`
- [ ] Described in the agent's `## TOOLS` section; unit-tested
