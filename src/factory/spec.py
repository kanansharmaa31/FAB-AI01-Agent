"""AgentSpec: the declarative contract for one mesh agent.

An agent is fully described by ``src/agents/<name>/agent.spec.yaml`` + ``prompt.md``.
Every model uses ``extra="forbid"`` so a typo in a spec (``tool:`` instead of
``tools:``) fails at load time rather than silently producing a weaker agent.

Validation here is *local* (one spec in isolation). Cross-spec checks (port
clashes, unknown peer nodes, importable function refs, prompt file present,
MCP tool names) live in :mod:`src.factory.loader`.
"""
from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.integrations.mcp_catalog import SERVICE_CATALOG

SNAKE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
FUNCTION_REF = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
DOTTED_REF = re.compile(r"^[A-Za-z_][\w.]*[.:][A-Za-z_]\w*$")

AgentKind = Literal["specialist", "mcp_thin", "coordinator", "guardrail"]
McpService = Literal["transactions", "kyc", "screening", "graph", "policy_rag"]
ModelProfileName = Literal["default", "reasoning", "fast"]
Provider = Literal["openai_compat", "azure_openai", "foundry", "anthropic", "ollama", "stub"]

DEFAULT_MIDDLEWARE = ["audit", "tipping_off"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelProfile(_Strict):
    """Model selection. ``profile`` maps to LLM_MODEL_<PROFILE>; explicit fields win.

    Resolution order (first non-empty): ``<NODE>_MODEL`` env, ``model`` here,
    ``LLM_MODEL_<PROFILE>``, ``LLM_MODEL_DEFAULT``.
    """

    profile: ModelProfileName = "default"
    provider: Provider | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)


class FunctionToolRef(_Strict):
    """In-process Python tool, referenced as ``module.path:attribute``."""

    type: Literal["function"]
    ref: str

    @field_validator("ref")
    @classmethod
    def _ref_format(cls, v: str) -> str:
        if not FUNCTION_REF.match(v):
            raise ValueError(f"function ref '{v}' must look like 'package.module:attribute'")
        return v


class McpToolRef(_Strict):
    """Tools from an MCP service. ``allow`` is mandatory: least privilege (ISTR rule 5)."""

    type: Literal["mcp"]
    service: McpService
    allow: list[str] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _allow_in_catalog(self) -> "McpToolRef":
        known = set(SERVICE_CATALOG[self.service])
        unknown = [t for t in self.allow if t not in known]
        if unknown:
            raise ValueError(
                f"mcp service '{self.service}' has no tool(s) {unknown}; known: {sorted(known)}"
            )
        if len(set(self.allow)) != len(self.allow):
            raise ValueError(f"duplicate tool names in allow list for '{self.service}'")
        return self


class A2APeerToolRef(_Strict):
    """Another mesh node exposed as a tool (agent-as-tool over A2A)."""

    type: Literal["a2a_peer"]
    node: str
    max_depth: int = Field(default=2, ge=1, le=5)
    description: str | None = None

    @field_validator("node")
    @classmethod
    def _node_name(cls, v: str) -> str:
        if not SNAKE.match(v):
            raise ValueError(f"peer node '{v}' must be snake_case")
        return v


ToolRef = Annotated[Union[FunctionToolRef, McpToolRef, A2APeerToolRef], Field(discriminator="type")]


class GuardrailSpec(_Strict):
    """Deterministic checks wrapped around the agent (names from deterministic_filters.CHECKS)."""

    input: list[str] = Field(default_factory=lambda: ["prompt_injection"])
    output: list[str] = Field(default_factory=lambda: ["tipping_off", "filing_action"])
    pii_redaction: bool = True


class MemorySpec(_Strict):
    """Conversation memory replayed into the agent's prompt (per case/session)."""

    enabled: bool = False
    backend: Literal["jsonl", "redis"] = "jsonl"
    max_turns: int = Field(default=3, ge=0, le=50)


class A2ASkill(_Strict):
    id: str
    name: str
    description: str


class A2ASpec(_Strict):
    port: int = Field(..., ge=1024, le=65535)
    skills: list[A2ASkill] = Field(default_factory=list)


class ObservabilitySpec(_Strict):
    span_name: str | None = None
    tags: list[str] = Field(default_factory=list)


class AgentSpec(_Strict):
    name: str
    public_name: str
    description: str = Field(..., min_length=10)
    version: str = "0.1.0"
    owner: str
    kind: AgentKind

    instructions_file: str = "prompt.md"
    prompt_vars: dict[str, str] = Field(default_factory=dict)
    # Shared prompt fragments from src/agents/_shared/<name>.md appended to the prompt.
    includes: list[str] = Field(default_factory=lambda: ["istr_rules"])

    model: ModelProfile = Field(default_factory=ModelProfile)
    tools: list[ToolRef] = Field(default_factory=list)
    middleware: list[str] = Field(default_factory=lambda: list(DEFAULT_MIDDLEWARE))
    max_tool_calls: int = Field(default=15, ge=1, le=100)
    guardrails: GuardrailSpec = Field(default_factory=GuardrailSpec)
    memory: MemorySpec = Field(default_factory=MemorySpec)
    output_schema: str | None = None
    reasoning_trace: bool = True
    a2a: A2ASpec
    observability: ObservabilitySpec = Field(default_factory=ObservabilitySpec)

    @field_validator("name")
    @classmethod
    def _snake(cls, v: str) -> str:
        if not SNAKE.match(v):
            raise ValueError(f"name '{v}' must be snake_case (a-z, 0-9, _), 2-48 chars")
        return v

    @field_validator("public_name")
    @classmethod
    def _public(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z][A-Za-z0-9]{1,63}$", v):
            raise ValueError(f"public_name '{v}' must be alphanumeric PascalCase, e.g. 'TransactionAnalysisAgent'")
        return v

    @field_validator("output_schema")
    @classmethod
    def _dotted(cls, v: str | None) -> str | None:
        if v is not None and not DOTTED_REF.match(v):
            raise ValueError(f"output_schema '{v}' must be a dotted path, e.g. 'src.schemas.istr.ISTRDraft'")
        return v

    @model_validator(mode="after")
    def _kind_rules(self) -> "AgentSpec":
        mcp = [t for t in self.tools if t.type == "mcp"]
        peers = [t for t in self.tools if t.type == "a2a_peer"]
        if self.kind == "mcp_thin" and not mcp:
            raise ValueError("kind 'mcp_thin' requires at least one tool of type 'mcp'")
        if self.kind == "coordinator" and not peers:
            raise ValueError("kind 'coordinator' requires at least one tool of type 'a2a_peer'")
        if self.kind == "guardrail" and self.tools:
            raise ValueError("kind 'guardrail' agents must not have tools (they only judge text)")
        if any(p.node == self.name for p in peers):
            raise ValueError("an agent cannot list itself as an a2a_peer")
        services = [t.service for t in mcp]
        if len(set(services)) != len(services):
            raise ValueError("each mcp service may appear once; merge the allow lists")
        if len(set(self.middleware)) != len(self.middleware):
            raise ValueError("duplicate middleware names")
        return self

    # Convenience views used by the factory / registry -----------------------
    @property
    def mcp_tools(self) -> list[McpToolRef]:
        return [t for t in self.tools if isinstance(t, McpToolRef)]

    @property
    def peer_tools(self) -> list[A2APeerToolRef]:
        return [t for t in self.tools if isinstance(t, A2APeerToolRef)]

    @property
    def function_tools(self) -> list[FunctionToolRef]:
        return [t for t in self.tools if isinstance(t, FunctionToolRef)]

    @property
    def is_mcp_backed(self) -> bool:
        return bool(self.mcp_tools)
