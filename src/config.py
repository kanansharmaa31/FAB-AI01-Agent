"""Env-driven configuration for the ISTR agent mesh.

Mirrors the reference repo's ``Config`` class (one flat, importable namespace read
from ``.env``) but removes every provider hardcode: the LLM provider, model and
key are resolved per agent at build time so a single ``.env`` can run the whole
mesh on the offline ``stub`` provider, a local OpenAI-compatible server, or a
bank-approved cloud endpoint.

Per-agent overrides are looked up dynamically (``os.environ`` at call time, not
import time) so tests and the scaffolder see changes without re-importing:

    <NODE>_PROVIDER, <NODE>_MODEL, <NODE>_API_KEY, PORT_<NODE>

where ``<NODE>`` is the upper-cased node name, e.g. ``TRANSACTION_ANALYSIS_MODEL``.
"""
from __future__ import annotations

import os
import pathlib

from dotenv import load_dotenv

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

# The project .env wins over stale shell variables, as in the reference. Tests set
# ISTR_SKIP_DOTENV=1 so a developer's local .env never leaks into the offline suite.
if os.getenv("ISTR_SKIP_DOTENV", "").lower() not in ("1", "true", "yes"):
    load_dotenv(PROJECT_ROOT / ".env", override=True)


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class Config:
    # ------------------------------------------------------------------
    # LLM provider defaults (resolved per agent by src/factory/model_clients.py)
    # ------------------------------------------------------------------
    # openai_compat | azure_openai | foundry | anthropic | ollama | stub
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai_compat").strip().lower()
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")

    # Model profiles referenced by AgentSpec.model.profile.
    LLM_MODEL_DEFAULT: str = os.getenv("LLM_MODEL_DEFAULT", "")
    LLM_MODEL_REASONING: str = os.getenv("LLM_MODEL_REASONING", "")
    LLM_MODEL_FAST: str = os.getenv("LLM_MODEL_FAST", "")

    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    AZURE_USE_ENTRA_ID: bool = _flag("AZURE_USE_ENTRA_ID", "false")

    # Azure AI Foundry
    FOUNDRY_PROJECT_ENDPOINT: str = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")

    # Anthropic
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Ollama (native client; for Ollama's OpenAI-compatible endpoint use openai_compat)
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")

    # ------------------------------------------------------------------
    # Agent definitions + runtime paths
    # ------------------------------------------------------------------
    AGENTS_DIR: str = os.getenv("AGENTS_DIR", str(PROJECT_ROOT / "src" / "agents"))
    SYNTHETIC_DATA_DIR: str = os.getenv("SYNTHETIC_DATA_DIR", str(PROJECT_ROOT / "data" / "synthetic"))
    AUDIT_LOG_FILE: str = os.getenv("AUDIT_LOG_FILE", "data/logs/audit_trail.jsonl")
    CONVERSATION_STORE_DIR: str = os.getenv("CONVERSATION_STORE_DIR", "data/conversations")
    LOG_FILE: str = os.getenv("LOG_FILE", "data/logs/istr_mesh.log")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_JSON: bool = _flag("LOG_JSON", "false")

    # ------------------------------------------------------------------
    # Conversation memory (per case_id / session_id)
    # ------------------------------------------------------------------
    ENABLE_CONVERSATION_MEMORY: bool = _flag("ENABLE_CONVERSATION_MEMORY", "true")
    CONVERSATION_BACKEND: str = os.getenv("CONVERSATION_BACKEND", "jsonl")
    CONVERSATION_MAX_TURNS: int = int(os.getenv("CONVERSATION_MAX_TURNS", "3"))
    CONVERSATION_REDIS_URL: str = os.getenv("CONVERSATION_REDIS_URL", "redis://127.0.0.1:6379/0")

    # ------------------------------------------------------------------
    # Workflow wiring: which agent nodes the pipeline calls. Agents are
    # added/removed under src/agents/; these names say which ones the workflow uses.
    # ------------------------------------------------------------------
    # Node that runs the investigation (delegates to specialists).
    WORKFLOW_COORDINATOR_NODE: str = os.getenv("WORKFLOW_COORDINATOR_NODE", "example_coordinator").strip()
    # Node that gives the semantic COMPLIANCE_PASSED/FAILED verdict. Empty = no semantic
    # guard agent: the step is skipped (and logged); deterministic gates + RBAC still run.
    WORKFLOW_COMPLIANCE_NODE: str = os.getenv("WORKFLOW_COMPLIANCE_NODE", "").strip()

    # ------------------------------------------------------------------
    # Feature flags
    # ------------------------------------------------------------------
    ENABLE_COMPLIANCE: bool = _flag("ENABLE_COMPLIANCE", "true")
    # Controls draft *quality checks* only. The draft-only status
    # (PENDING_MLRO_REVIEW) is structural and cannot be switched off.
    ENABLE_HUMAN_REVIEW_GATE: bool = _flag("ENABLE_HUMAN_REVIEW_GATE", "true")
    # Never log raw prompts/outputs unless explicitly enabled (ISTR rule 4).
    ENABLE_SENSITIVE_DATA: bool = _flag("ENABLE_SENSITIVE_DATA", "false")
    # Default circuit breaker for the token_budget middleware (per agent run).
    MAX_TOOL_CALLS_PER_RUN: int = int(os.getenv("MAX_TOOL_CALLS_PER_RUN", "15"))

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------
    OBS_PROFILE: str = os.getenv("OBS_PROFILE", "off").lower()  # dev | prod | off
    ENABLE_INSTRUMENTATION: bool = _flag("ENABLE_INSTRUMENTATION", "true")
    ENABLE_CONSOLE_EXPORTERS: bool = _flag("ENABLE_CONSOLE_EXPORTERS", "false")
    OTEL_EXPORTER_OTLP_ENDPOINT: str = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    OTEL_SERVICE_NAME: str = os.getenv("OTEL_SERVICE_NAME", "istr_mesh")
    APPLICATIONINSIGHTS_CONNECTION_STRING: str = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")

    OPIK_ENABLED: bool = _flag("OPIK_ENABLED", "false")
    OPIK_URL_OVERRIDE: str = os.getenv("OPIK_URL_OVERRIDE", "")  # self-hosted Opik base URL
    OPIK_API_KEY: str = os.getenv("OPIK_API_KEY", "")
    OPIK_WORKSPACE: str = os.getenv("OPIK_WORKSPACE", "")
    OPIK_PROJECT_NAME: str = os.getenv("OPIK_PROJECT_NAME", "istr-agent-mesh")

    # ------------------------------------------------------------------
    # Mesh networking
    # ------------------------------------------------------------------
    A2A_HOST: str = os.getenv("A2A_HOST", "127.0.0.1")
    A2A_TIMEOUT: float = float(os.getenv("A2A_TIMEOUT", "120.0"))
    API_HOST: str = os.getenv("API_HOST", "127.0.0.1")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))

    DEVUI_HOST: str = os.getenv("DEVUI_HOST", "127.0.0.1")
    DEVUI_PORT: int = int(os.getenv("DEVUI_PORT", "8090"))
    DEVUI_USER: str = os.getenv("DEVUI_USER", "analyst1")

    # ------------------------------------------------------------------
    # MCP services. Mock services run locally; swap a URL to point a node at the
    # real GERNAS service (SQL Agent / KG / RAG) without touching any agent code.
    # ------------------------------------------------------------------
    MCP_SERVICE_URLS: dict[str, str] = {
        "transactions": os.getenv("TRANSACTIONS_MCP_URL", "http://127.0.0.1:9101/mcp"),
        "kyc": os.getenv("KYC_MCP_URL", "http://127.0.0.1:9102/mcp"),
        "screening": os.getenv("SCREENING_MCP_URL", "http://127.0.0.1:9103/mcp"),
        "graph": os.getenv("GRAPH_MCP_URL", "http://127.0.0.1:9104/mcp"),
        "policy_rag": os.getenv("POLICY_RAG_MCP_URL", "http://127.0.0.1:9105/mcp"),
    }
    MCP_REQUEST_TIMEOUT: int = int(os.getenv("MCP_REQUEST_TIMEOUT", "30"))
    MCP_API_KEY: str = os.getenv("MCP_API_KEY", "")  # forwarded as X-API-Key when set

    # ------------------------------------------------------------------
    # Per-agent dynamic lookups
    # ------------------------------------------------------------------
    @staticmethod
    def env_key(node: str, suffix: str) -> str:
        """``example_specialist`` + ``MODEL`` -> ``EXAMPLE_SPECIALIST_MODEL``."""
        return f"{node.upper()}_{suffix}"

    @classmethod
    def agent_override(cls, node: str, suffix: str) -> str:
        return os.getenv(cls.env_key(node, suffix), "").strip()

    @classmethod
    def port_override(cls, node: str) -> int | None:
        raw = os.getenv(f"PORT_{node.upper()}", "").strip()
        return int(raw) if raw else None

    @classmethod
    def profile_model(cls, profile: str) -> str:
        """Model name for a profile, falling back to the default profile."""
        by_profile = {
            "default": cls.LLM_MODEL_DEFAULT,
            "reasoning": cls.LLM_MODEL_REASONING,
            "fast": cls.LLM_MODEL_FAST,
        }
        return by_profile.get(profile) or cls.LLM_MODEL_DEFAULT

    @classmethod
    def mcp_url(cls, service: str) -> str:
        try:
            return cls.MCP_SERVICE_URLS[service]
        except KeyError as exc:
            raise ValueError(
                f"Unknown MCP service '{service}'. Known: {', '.join(sorted(cls.MCP_SERVICE_URLS))}"
            ) from exc

    @classmethod
    def agent_url(cls, node: str) -> str:
        """Base A2A URL for a node. Ports come from the agent specs (+ PORT_ overrides)."""
        from src.agents.node_registry import NODE_PORTS  # lazy: registry imports Config

        return f"http://{cls.A2A_HOST}:{NODE_PORTS[node]}/"

    @classmethod
    def resolve_path(cls, path: str) -> pathlib.Path:
        """Resolves relative runtime paths against the project root (CWD-independent)."""
        p = pathlib.Path(path)
        return p if p.is_absolute() else PROJECT_ROOT / p
