"""Provider -> MAF chat client. No provider is hardcoded.

Resolution per agent (first non-empty wins):

  provider: ``<NODE>_PROVIDER`` env > ``spec.model.provider`` > ``LLM_PROVIDER``
  model:    ``<NODE>_MODEL`` env    > ``spec.model.model``    > ``LLM_MODEL_<PROFILE>`` > ``LLM_MODEL_DEFAULT``
  api key:  ``<NODE>_API_KEY`` env  > provider default key (``LLM_API_KEY``, ``AZURE_OPENAI_API_KEY`` ...)

Providers:

=================  ==========================================================  =======================
``openai_compat``  ``OpenAIChatCompletionClient`` + ``LLM_BASE_URL``            core (installed)
``azure_openai``   ``OpenAIChatCompletionClient`` + ``azure_endpoint``          core (installed)
``foundry``        ``agent_framework.foundry.FoundryChatClient``                agent-framework-foundry
``anthropic``      ``agent_framework.anthropic.AnthropicClient``                agent-framework-anthropic
``ollama``         ``agent_framework.ollama.OllamaChatClient``                  agent-framework-ollama
``stub``           deterministic offline client (tests, smoke test, demos)     built in
=================  ==========================================================  =======================

Optional extensions are imported lazily and constructed with only the keyword
arguments their installed signature accepts, so an extension version change
surfaces as a clear error rather than a TypeError deep inside MAF.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from src.config import Config
from src.factory.spec import AgentSpec

PROVIDERS = ("openai_compat", "azure_openai", "foundry", "anthropic", "ollama", "stub")


class ModelConfigError(RuntimeError):
    """Raised at build time (fail fast) when a provider/model/key is missing or unusable."""


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model: str
    api_key: str
    temperature: float | None
    max_tokens: int | None

    def describe(self) -> str:
        key = f"***{self.api_key[-4:]}" if self.api_key else "<none>"
        return f"provider={self.provider} model={self.model or '<default>'} key={key}"


def resolve_model(spec: AgentSpec) -> ResolvedModel:
    provider = (Config.agent_override(spec.name, "PROVIDER") or spec.model.provider or Config.LLM_PROVIDER).lower()
    if provider not in PROVIDERS:
        raise ModelConfigError(f"[{spec.name}] unknown LLM provider '{provider}'. Valid: {', '.join(PROVIDERS)}")
    model = (Config.agent_override(spec.name, "MODEL") or spec.model.model
             or Config.profile_model(spec.model.profile))
    default_keys = {
        "openai_compat": Config.LLM_API_KEY,
        "azure_openai": Config.AZURE_OPENAI_API_KEY,
        "anthropic": Config.ANTHROPIC_API_KEY,
    }
    api_key = Config.agent_override(spec.name, "API_KEY") or default_keys.get(provider, "")
    return ResolvedModel(provider, model or "", api_key, spec.model.temperature, spec.model.max_tokens)


def _construct(cls: type, **kwargs: Any) -> Any:
    """Instantiate with only the kwargs ``cls`` accepts (unless it takes **kwargs)."""
    params = inspect.signature(cls.__init__).parameters
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kwargs = {k: v for k, v in kwargs.items() if k in params}
    return cls(**{k: v for k, v in kwargs.items() if v not in (None, "")})


def _require(cond: bool, spec: AgentSpec, msg: str) -> None:
    if not cond:
        raise ModelConfigError(f"[{spec.name}] {msg}")


def _optional(module: str, attr: str, package: str, spec: AgentSpec) -> type:
    try:
        mod = __import__(module, fromlist=[attr])
        return getattr(mod, attr)
    except (ImportError, ModuleNotFoundError, AttributeError) as exc:
        raise ModelConfigError(f"[{spec.name}] provider needs '{package}': pip install --pre {package} ({exc})") from exc


def build_chat_client(spec: AgentSpec) -> Any:
    """Returns a MAF chat client for ``spec``; raises :class:`ModelConfigError` with a fix hint."""
    r = resolve_model(spec)

    if r.provider == "stub":
        from src.factory.stub_client import StubChatClient

        return StubChatClient(node=spec.name, kind=spec.kind, structured=bool(spec.output_schema),
                              reasoning_trace=spec.reasoning_trace)

    if r.provider == "openai_compat":
        from agent_framework.openai import OpenAIChatCompletionClient

        _require(bool(r.model), spec, "no model configured: set LLM_MODEL_DEFAULT or "
                 f"{Config.env_key(spec.name, 'MODEL')}")
        _require(bool(r.api_key), spec, f"no API key: set LLM_API_KEY or {Config.env_key(spec.name, 'API_KEY')} "
                 "(local OpenAI-compatible servers accept any non-empty value), or use LLM_PROVIDER=stub offline")
        return OpenAIChatCompletionClient(model=r.model, api_key=r.api_key, base_url=Config.LLM_BASE_URL)

    if r.provider == "azure_openai":
        from agent_framework.openai import OpenAIChatCompletionClient

        _require(bool(Config.AZURE_OPENAI_ENDPOINT), spec, "AZURE_OPENAI_ENDPOINT is not set")
        _require(bool(r.model), spec, "no deployment name: set LLM_MODEL_DEFAULT or "
                 f"{Config.env_key(spec.name, 'MODEL')}")
        kwargs: dict[str, Any] = dict(model=r.model, azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
                                      api_version=Config.AZURE_OPENAI_API_VERSION)
        if r.api_key:
            kwargs["api_key"] = r.api_key
        else:
            _require(Config.AZURE_USE_ENTRA_ID, spec,
                     "no AZURE_OPENAI_API_KEY; set it or AZURE_USE_ENTRA_ID=true (requires azure-identity)")
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:
                raise ModelConfigError(f"[{spec.name}] AZURE_USE_ENTRA_ID=true needs azure-identity") from exc
            kwargs["credential"] = DefaultAzureCredential()
        return OpenAIChatCompletionClient(**kwargs)

    if r.provider == "foundry":
        cls = _optional("agent_framework.foundry", "FoundryChatClient", "agent-framework-foundry", spec)
        _require(bool(Config.FOUNDRY_PROJECT_ENDPOINT), spec, "FOUNDRY_PROJECT_ENDPOINT is not set")
        try:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        except ImportError as exc:
            raise ModelConfigError(f"[{spec.name}] foundry provider needs azure-identity") from exc
        return _construct(cls, project_endpoint=Config.FOUNDRY_PROJECT_ENDPOINT, model=r.model,
                          model_deployment_name=r.model, credential=credential)

    if r.provider == "anthropic":
        cls = _optional("agent_framework.anthropic", "AnthropicClient", "agent-framework-anthropic", spec)
        _require(bool(r.api_key), spec, f"set ANTHROPIC_API_KEY or {Config.env_key(spec.name, 'API_KEY')}")
        _require(bool(r.model), spec, "no model configured: set LLM_MODEL_DEFAULT")
        return _construct(cls, model=r.model, model_id=r.model, api_key=r.api_key)

    # ollama
    cls = _optional("agent_framework.ollama", "OllamaChatClient", "agent-framework-ollama", spec)
    _require(bool(r.model), spec, "no model configured: set LLM_MODEL_DEFAULT (e.g. llama3.1:8b)")
    return _construct(cls, host=Config.OLLAMA_HOST, model=r.model, model_id=r.model)


def default_options(spec: AgentSpec) -> dict[str, Any]:
    """Per-agent chat options (temperature / max_tokens) passed as ``Agent(default_options=...)``."""
    r = resolve_model(spec)
    opts: dict[str, Any] = {}
    if r.temperature is not None:
        opts["temperature"] = r.temperature
    if r.max_tokens is not None:
        opts["max_tokens"] = r.max_tokens
    return opts
