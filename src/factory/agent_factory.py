"""AgentFactory: AgentSpec -> running MAF ``Agent``.

Public API::

    factory = AgentFactory(config=Config)
    agent = factory.create("example_specialist", log_path=None, mcp_tools=None)
    agent, public_name, description = factory.build_node("example_specialist")
    agent = factory.create_from_spec(spec, instructions="...")       # ad-hoc / tests
    factory.list_specs()

Builds ``Agent(client=..., name=..., instructions=..., tools=..., middleware=[...])``
exactly as the reference ``create_demo_agent`` does, but every input comes from the
spec: model client (provider-pluggable), tools (function / MCP / A2A peer),
middleware stack, guardrails and rendered instructions.

Testability: pass ``chat_client_factory=lambda spec: FakeClient()`` to build
agents with no network and no API key.
"""
from __future__ import annotations

import pathlib
from typing import Any, Callable

from agent_framework import Agent

from src.config import Config
from src.factory.loader import SpecLoader
from src.factory.middleware_registry import build_middleware
from src.factory.model_clients import build_chat_client, default_options, resolve_model
from src.factory.spec import AgentSpec
from src.factory.tool_resolver import ToolResolver
from src.observability import CAT_FACTORY, get_logger

_log = get_logger(CAT_FACTORY)

ChatClientFactory = Callable[[AgentSpec], Any]


class AgentFactory:
    def __init__(
        self,
        config: type[Config] = Config,
        agents_dir: str | pathlib.Path | None = None,
        chat_client_factory: ChatClientFactory | None = None,
    ):
        self.config = config
        self.loader = SpecLoader(agents_dir or config.AGENTS_DIR)
        self._client_factory = chat_client_factory or build_chat_client

    # ------------------------------------------------------------- queries
    def list_specs(self) -> list[AgentSpec]:
        return list(self.loader.specs().values())

    def get_spec(self, name: str) -> AgentSpec:
        return self.loader.get(name).spec

    def instructions_for(self, name: str) -> str:
        la = self.loader.get(name)
        return self.loader.render_instructions(la.spec, la.prompt_template)

    # -------------------------------------------------------------- builds
    def create(self, name: str, log_path: str | None = None, mcp_tools: dict[str, Any] | None = None) -> Agent:
        """Builds the named agent from ``src/agents/<name>/`` (spec + prompt)."""
        la = self.loader.get(name)
        instructions = self.loader.render_instructions(la.spec, la.prompt_template)
        return self.create_from_spec(la.spec, instructions=instructions, log_path=log_path, mcp_tools=mcp_tools)

    def build_node(self, name: str, log_path: str | None = None,
                   mcp_tools: dict[str, Any] | None = None) -> tuple[Agent, str, str]:
        """Registry-compatible: ``(agent, public_name, description)`` like the reference."""
        agent = self.create(name, log_path=log_path, mcp_tools=mcp_tools)
        spec = self.get_spec(name)
        return agent, spec.public_name, spec.description

    def create_from_spec(
        self,
        spec: AgentSpec,
        instructions: str | None = None,
        log_path: str | None = None,
        mcp_tools: dict[str, Any] | None = None,
    ) -> Agent:
        """Builds an agent from an in-memory spec (no files needed when ``instructions`` is given)."""
        if instructions is None:
            base = self.loader.agents_dir / spec.name / spec.instructions_file
            if not base.is_file():
                raise FileNotFoundError(f"[{spec.name}] instructions not provided and {base} not found")
            instructions = self.loader.render_instructions(spec, base.read_text(encoding="utf-8"))

        client = self._client_factory(spec)
        self._apply_tool_call_backstop(client, spec)
        tools = ToolResolver(self._peer_descriptions()).resolve(spec, mcp_tools)
        middleware = build_middleware(spec, log_path)

        kwargs: dict[str, Any] = dict(
            client=client,
            name=spec.public_name,
            description=spec.description,
            instructions=instructions,
            middleware=middleware,
            additional_properties={"node": spec.name, "kind": spec.kind, "version": spec.version,
                                   "tags": spec.observability.tags},
        )
        if tools:
            kwargs["tools"] = tools
        opts = default_options(spec) if self._client_factory is build_chat_client else {}
        if opts:
            kwargs["default_options"] = opts
        agent = Agent(**kwargs)
        _log.info("built agent node=%s kind=%s tools=%d middleware=%d %s", spec.name, spec.kind, len(tools),
                  len(middleware), resolve_model(spec).describe() if self._client_factory is build_chat_client
                  else "client=injected")
        return agent

    # ------------------------------------------------------------- helpers
    def _peer_descriptions(self) -> dict[str, str]:
        try:
            return {n: s.description for n, s in self.loader.specs().items()}
        except Exception:
            return {}

    @staticmethod
    def _apply_tool_call_backstop(client: Any, spec: AgentSpec) -> None:
        """MAF-native hard cap above the token_budget middleware's soft cap."""
        cfg = getattr(client, "function_invocation_configuration", None)
        if isinstance(cfg, dict):
            cfg["max_function_calls"] = spec.max_tool_calls + 5


_DEFAULT: AgentFactory | None = None


def default_factory() -> AgentFactory:
    """Process-wide factory over ``Config.AGENTS_DIR``; used by every ``src/agents/<name>/agent.py``."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = AgentFactory()
    return _DEFAULT
