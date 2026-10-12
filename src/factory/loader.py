"""Discovers, validates and renders the agent definitions.

Layout (one folder per agent)::

    src/agents/
      _shared/istr_rules.md        # appended to every prompt (spec.includes)
      _shared/reasoning_trace.md   # appended when spec.reasoning_trace is true
      _template/*.tmpl             # scaffolder templates (ignored here)
      <name>/agent.py              # Python entry point: get_<name>_agent() + agent-specific tools
      <name>/agent.spec.yaml       # configuration: tools, model, port, guardrails
      <name>/prompt.md             # instructions

Validation fails loudly and reports *every* problem at once (with file paths),
because a mesh that half-loads is worse than one that refuses to start. Checks:

- schema (pydantic, ``extra="forbid"``) and directory name == spec name
- ``agent.py`` exists, imports, and defines ``get_<name>_agent``
- prompt file exists, has all mandatory sections, all ``{{ var }}`` resolve
- unique names, no port clashes (after ``PORT_<NODE>`` overrides)
- ``a2a_peer`` nodes exist; ``function`` refs import; ``output_schema`` imports
- middleware / guardrail / include names are known
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import pathlib
import re
import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from src.config import PROJECT_ROOT, Config
from src.factory.spec import AgentSpec

SPEC_FILE = "agent.spec.yaml"
ENTRY_FILE = "agent.py"
SHARED_DIR = "_shared"

REQUIRED_SECTIONS = (
    "ROLE",
    "SCOPE",
    "TOOLS",
    "OPERATING RULES",
    "EVIDENCE & CITATION RULES",
    "REFUSAL/ESCALATION",
    "OUTPUT FORMAT",
    "REASONING TRANSPARENCY",
)

_VAR = re.compile(r"\{\{\s*([A-Za-z_][\w]*)\s*\}\}")


class SpecValidationError(ValueError):
    """Raised with every validation problem found, one per line."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Agent spec validation failed:\n  - " + "\n  - ".join(errors))


@dataclass
class LoadedAgent:
    spec: AgentSpec
    directory: pathlib.Path
    prompt_template: str
    errors: list[str] = field(default_factory=list)
    module: ModuleType | None = None  # the agent's agent.py

    @property
    def builder(self) -> Any:
        """The agent's ``get_<name>_agent`` entry point."""
        return getattr(self.module, builder_name(self.spec.name), None)


def builder_name(name: str) -> str:
    return f"get_{name}_agent"


def load_agent_module(directory: pathlib.Path) -> ModuleType:
    """Imports ``<directory>/agent.py``.

    Agents inside the project's ``src/agents`` are imported by their normal dotted
    path (``src.agents.<name>.agent``) so ``from src.agents.x.agent import ...``
    elsewhere returns the same module. Folders outside the project (test copies)
    are loaded from the file under a unique private module name.
    """
    path = directory / ENTRY_FILE
    try:
        rel = directory.resolve().relative_to(PROJECT_ROOT)
        if rel.parts[:2] == ("src", "agents") and len(rel.parts) == 3:
            return importlib.import_module(f"src.agents.{directory.name}.agent")
    except ValueError:
        pass
    unique = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:10]
    mod_name = f"_istr_agent_{directory.name}_{unique}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def import_ref(ref: str) -> Any:
    """Imports ``pkg.mod:attr`` or ``pkg.mod.attr``."""
    if ":" in ref:
        mod, attr = ref.split(":", 1)
    else:
        mod, _, attr = ref.rpartition(".")
    return getattr(importlib.import_module(mod), attr)


def effective_port(spec: AgentSpec) -> int:
    return Config.port_override(spec.name) or spec.a2a.port


def _prompt_sections(text: str) -> set[str]:
    return {m.group(1).strip().upper() for m in re.finditer(r"^#{1,3}\s+(.+?)\s*$", text, re.MULTILINE)}


def tool_summary(spec: AgentSpec) -> str:
    """Human-readable tool list injected as ``{{ tool_list }}``."""
    lines = []
    for t in spec.tools:
        if t.type == "function":
            lines.append(f"- `{t.ref.split(':')[-1]}` (function)")
        elif t.type == "mcp":
            lines.append(f"- MCP `{t.service}`: {', '.join(f'`{a}`' for a in t.allow)}")
        else:
            lines.append(f"- `consult_{t.node}` (A2A peer agent `{t.node}`)")
    return "\n".join(lines) or "- (no tools: reason over the provided text only)"


class SpecLoader:
    """Loads every ``src/agents/*/agent.spec.yaml`` (+ agent.py, prompt.md) and validates the set."""

    def __init__(self, agents_dir: str | pathlib.Path | None = None):
        self.agents_dir = pathlib.Path(agents_dir or Config.AGENTS_DIR)
        self._agents: dict[str, LoadedAgent] | None = None

    # ------------------------------------------------------------------ load
    def spec_dirs(self) -> list[pathlib.Path]:
        if not self.agents_dir.is_dir():
            raise SpecValidationError([f"agents directory not found: {self.agents_dir}"])
        return sorted(
            p for p in self.agents_dir.iterdir()
            if p.is_dir() and not p.name.startswith(("_", ".")) and (p / SPEC_FILE).exists()
        )

    @staticmethod
    def parse_spec(data: dict[str, Any], source: str) -> tuple[AgentSpec | None, list[str]]:
        try:
            return AgentSpec.model_validate(data), []
        except ValidationError as exc:
            errs = []
            for e in exc.errors():
                loc = ".".join(str(x) for x in e["loc"]) or "<root>"
                errs.append(f"{source}: {loc}: {e['msg']}")
            return None, errs

    def _load_one(self, directory: pathlib.Path) -> tuple[LoadedAgent | None, list[str]]:
        path = directory / SPEC_FILE
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            return None, [f"{path}: invalid YAML: {exc}"]
        if not isinstance(data, dict):
            return None, [f"{path}: top level must be a mapping"]
        spec, errs = self.parse_spec(data, str(path))
        if spec is None:
            return None, errs
        if spec.name != directory.name:
            errs.append(f"{path}: name '{spec.name}' must equal its directory name '{directory.name}'")

        module = None
        entry = directory / ENTRY_FILE
        if not entry.is_file():
            errs.append(f"{entry}: missing agent entry point (create it with scripts/new_agent.py "
                        f"or copy another agent's {ENTRY_FILE})")
        else:
            try:
                module = load_agent_module(directory)
                if not callable(getattr(module, builder_name(spec.name), None)):
                    errs.append(f"{entry}: must define {builder_name(spec.name)}(log_path=None, mcp_tools=None)")
            except Exception as exc:
                errs.append(f"{entry}: cannot be imported ({type(exc).__name__}: {exc})")

        prompt_path = directory / spec.instructions_file
        if not prompt_path.is_file():
            errs.append(f"{path}: instructions_file not found: {prompt_path}")
            return LoadedAgent(spec, directory, "", module=module), errs
        return LoadedAgent(spec, directory, prompt_path.read_text(encoding="utf-8"), module=module), errs

    def load(self, force: bool = False) -> dict[str, LoadedAgent]:
        """Loads and validates all specs; raises :class:`SpecValidationError` on any problem."""
        if self._agents is not None and not force:
            return self._agents
        agents: dict[str, LoadedAgent] = {}
        errors: list[str] = []
        for d in self.spec_dirs():
            loaded, errs = self._load_one(d)
            errors.extend(errs)
            if loaded is not None:
                if loaded.spec.name in agents:
                    errors.append(f"{d}: duplicate agent name '{loaded.spec.name}'")
                agents[loaded.spec.name] = loaded
        errors.extend(self.validate_set(agents))
        if errors:
            raise SpecValidationError(errors)
        self._agents = agents
        return agents

    # -------------------------------------------------------------- validate
    def validate_set(self, agents: dict[str, LoadedAgent]) -> list[str]:
        """Cross-spec validation. Also used by the scaffolder for a candidate set."""
        from src.factory.middleware_registry import MIDDLEWARE_BUILDERS
        from src.guardrails.deterministic_filters import CHECKS

        errors: list[str] = []
        ports: dict[int, str] = {}
        for name, la in agents.items():
            spec, where = la.spec, str(la.directory / SPEC_FILE)

            port = effective_port(spec)
            if port in ports:
                errors.append(f"{where}: port {port} clashes with agent '{ports[port]}'")
            ports[port] = name

            for peer in spec.peer_tools:
                if peer.node not in agents:
                    errors.append(f"{where}: a2a_peer node '{peer.node}' does not exist")
                elif agents[peer.node].spec.kind == "coordinator":
                    errors.append(f"{where}: a2a_peer '{peer.node}' is a coordinator; peers must be specialists")

            for fn in spec.function_tools:
                try:
                    obj = import_ref(fn.ref)
                    if not callable(obj):
                        errors.append(f"{where}: function ref '{fn.ref}' is not callable")
                except Exception as exc:
                    errors.append(f"{where}: function ref '{fn.ref}' cannot be imported ({exc})")

            if spec.output_schema:
                try:
                    model = import_ref(spec.output_schema)
                    if not (isinstance(model, type) and issubclass(model, BaseModel)):
                        errors.append(f"{where}: output_schema '{spec.output_schema}' is not a pydantic model")
                except Exception as exc:
                    errors.append(f"{where}: output_schema '{spec.output_schema}' cannot be imported ({exc})")

            for mw in spec.middleware:
                if mw not in MIDDLEWARE_BUILDERS:
                    errors.append(f"{where}: unknown middleware '{mw}'; known: {sorted(MIDDLEWARE_BUILDERS)}")
            for g in [*spec.guardrails.input, *spec.guardrails.output]:
                if g not in CHECKS:
                    errors.append(f"{where}: unknown guardrail '{g}'; known: {sorted(CHECKS)}")
            for inc in spec.includes:
                if not (self.agents_dir / SHARED_DIR / f"{inc}.md").is_file():
                    errors.append(f"{where}: include '{inc}' not found in {self.agents_dir / SHARED_DIR}")

            if la.prompt_template:
                missing = [s for s in REQUIRED_SECTIONS if s not in _prompt_sections(la.prompt_template)]
                if missing:
                    errors.append(f"{la.directory / spec.instructions_file}: missing prompt sections {missing}")
                unresolved = set(_VAR.findall(la.prompt_template)) - set(self.template_vars(spec))
                if unresolved:
                    errors.append(f"{la.directory / spec.instructions_file}: unresolved variables {sorted(unresolved)}")
        return errors

    # ---------------------------------------------------------------- render
    @staticmethod
    def template_vars(spec: AgentSpec) -> dict[str, str]:
        base = {
            "name": spec.name,
            "public_name": spec.public_name,
            "description": spec.description,
            "tool_list": tool_summary(spec),
            "max_tool_calls": str(spec.max_tool_calls),
        }
        return {**base, **spec.prompt_vars}

    @staticmethod
    def substitute(text: str, variables: dict[str, str]) -> str:
        return _VAR.sub(lambda m: variables.get(m.group(1), m.group(0)), text)

    def render_instructions(self, spec: AgentSpec, prompt_template: str) -> str:
        """Prompt + shared includes + reasoning block + output schema, variables substituted."""
        variables = self.template_vars(spec)
        parts = [prompt_template.strip()]
        includes = list(spec.includes) + (["reasoning_trace"] if spec.reasoning_trace else [])
        for inc in includes:
            path = self.agents_dir / SHARED_DIR / f"{inc}.md"
            if path.is_file():
                parts.append(path.read_text(encoding="utf-8").strip())
        if spec.output_schema:
            schema = import_ref(spec.output_schema).model_json_schema()
            parts.append(
                "## OUTPUT SCHEMA\nWhen you return the structured result, emit ONE fenced ```json block "
                "that validates against this JSON schema:\n```json\n"
                + json.dumps(schema, separators=(",", ":")) + "\n```"
            )
        return self.substitute("\n\n".join(parts), variables)

    # ------------------------------------------------------------- accessors
    def get(self, name: str) -> LoadedAgent:
        agents = self.load()
        if name not in agents:
            raise KeyError(f"Unknown agent '{name}'. Valid: {', '.join(sorted(agents))}")
        return agents[name]

    def specs(self) -> dict[str, AgentSpec]:
        return {n: la.spec for n, la in self.load().items()}
