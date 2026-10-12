"""Scaffold a new mesh agent from src/agents/_template/*.tmpl.

    python scripts/new_agent.py sanctions_deep_dive --kind mcp_thin \
        --tools mcp:screening,a2a_peer:transaction_analysis --port 8031

Creates ``src/agents/<name>/{agent.py,agent.spec.yaml,prompt.md}`` and
``tests/agents/test_<name>.py``, validates the new spec *together with every
existing spec* (unknown tool refs, port clashes, missing prompt sections,
unknown peers ...) and only then writes anything. Prints the next steps.

``--tools`` items (comma-separated):
    mcp:<service>                       allow = every tool in the service catalog (trim it!)
    mcp:<service>[tool_a|tool_b]        explicit allow list
    a2a_peer:<node>                     agent-as-tool (max_depth 1)
    function:<package.module:attr>      in-process Python tool

Flags: ``--dry-run`` prints the files instead of writing; ``--force`` overwrites.
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.factory.loader import SPEC_FILE, LoadedAgent, SpecLoader, SpecValidationError, effective_port  # noqa: E402
from src.factory.spec import SNAKE  # noqa: E402
from src.integrations.mcp_catalog import SERVICE_CATALOG  # noqa: E402

_PLACEHOLDER = re.compile(r"\[\[\s*([a-z_]+)\s*\]\]")
_MCP_ITEM = re.compile(r"^(?P<service>[a-z_]+)(?:\[(?P<allow>[\w|]+)\])?$")
_DEFAULT_PROFILE = {"coordinator": "reasoning", "guardrail": "fast", "mcp_thin": "default", "specialist": "default"}
_FIRST_PORT = 8020


class ScaffoldError(ValueError):
    pass


def render(template: str, values: dict[str, str]) -> str:
    def _sub(m: re.Match[str]) -> str:
        if m.group(1) not in values:
            raise ScaffoldError(f"template placeholder [[ {m.group(1)} ]] has no value")
        return values[m.group(1)]

    return _PLACEHOLDER.sub(_sub, template)


def parse_tools(raw: str) -> list[dict]:
    """``mcp:kyc,a2a_peer:x,function:pkg.mod:attr`` -> list of ToolRef dicts."""
    tools: list[dict] = []
    for item in [t.strip() for t in raw.split(",") if t.strip()]:
        kind, _, rest = item.partition(":")
        if kind == "mcp":
            m = _MCP_ITEM.match(rest)
            if not m:
                raise ScaffoldError(f"bad mcp tool '{item}'; use mcp:<service> or mcp:<service>[a|b]")
            service = m.group("service")
            if service not in SERVICE_CATALOG:
                raise ScaffoldError(f"unknown MCP service '{service}'; known: {', '.join(sorted(SERVICE_CATALOG))}")
            allow = m.group("allow").split("|") if m.group("allow") else list(SERVICE_CATALOG[service])
            tools.append({"type": "mcp", "service": service, "allow": allow})
        elif kind == "a2a_peer":
            tools.append({"type": "a2a_peer", "node": rest, "max_depth": 1})
        elif kind == "function":
            tools.append({"type": "function", "ref": rest})
        else:
            raise ScaffoldError(f"unknown tool type in '{item}'; use mcp:, a2a_peer: or function:")
    return tools


def tools_yaml(tools: list[dict]) -> str:
    if not tools:
        return " []"
    lines = []
    for t in tools:
        lines.append("\n  - " + yaml.safe_dump(t, default_flow_style=True, sort_keys=False, width=200).strip())
    return "".join(lines)


def next_free_port(loader: SpecLoader, existing: dict[str, LoadedAgent]) -> int:
    used = {effective_port(la.spec) for la in existing.values()} | {la.spec.a2a.port for la in existing.values()}
    port = max(used | {_FIRST_PORT - 1}) + 1
    while port in used:
        port += 1
    return port


def scaffold(args: argparse.Namespace) -> dict[pathlib.Path, str]:
    """Renders + validates. Returns {path: content}; writes nothing."""
    name = args.name
    if not SNAKE.match(name):
        raise ScaffoldError(f"name '{name}' must be snake_case (a-z, 0-9, _)")
    agents_dir = pathlib.Path(args.agents_dir)
    tests_dir = pathlib.Path(args.tests_dir)
    template_dir = agents_dir / "_template"
    loader = SpecLoader(agents_dir)
    existing = dict(loader.load())  # raises if the current set is already invalid

    target = agents_dir / name
    if name in existing and not args.force:
        raise ScaffoldError(f"agent '{name}' already exists at {target} (use --force to overwrite)")
    existing.pop(name, None)

    words = [w for w in name.split("_") if w != "agent"] or [name]
    public_name = args.public_name or "".join(w.capitalize() for w in words) + "Agent"
    description = args.description or f"TODO: describe what {public_name} does for ISTR investigations."
    port = args.port or next_free_port(loader, existing)
    tools = parse_tools(args.tools)
    values = {
        "name": name, "public_name": public_name, "description": description, "owner": args.owner,
        "kind": args.kind, "profile": args.profile or _DEFAULT_PROFILE[args.kind],
        "temperature": "0.0", "tools_yaml": tools_yaml(tools), "port": str(port),
        "env_prefix": name.upper(), "skill_name": public_name.removesuffix("Agent") or public_name,
        "created": datetime.date.today().isoformat(),
    }
    spec_text = render((template_dir / "agent.spec.yaml.tmpl").read_text(encoding="utf-8"), values)
    prompt_text = render((template_dir / "prompt.md.tmpl").read_text(encoding="utf-8"), values)
    test_text = render((template_dir / "test_agent.py.tmpl").read_text(encoding="utf-8"), values)
    agent_py = render((template_dir / "agent.py.tmpl").read_text(encoding="utf-8"),
                      {**values, "description": " ".join(description.split())})
    try:
        compile(agent_py, str(target / "agent.py"), "exec")
    except SyntaxError as exc:
        raise ScaffoldError(f"generated agent.py does not compile: {exc}") from exc

    spec, errors = SpecLoader.parse_spec(yaml.safe_load(spec_text), str(target / SPEC_FILE))
    if spec is None:
        raise SpecValidationError(errors)
    candidate = {**existing, name: LoadedAgent(spec, target, prompt_text)}
    errors = [e for e in loader.validate_set(candidate) if name in e or str(target) in e]
    if errors:
        raise SpecValidationError(errors)
    return {
        target / "agent.py": agent_py,
        target / SPEC_FILE: spec_text,
        target / "prompt.md": prompt_text,
        tests_dir / f"test_{name}.py": test_text,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Scaffold a new ISTR mesh agent.")
    p.add_argument("name", help="snake_case node name, e.g. trade_finance_review")
    p.add_argument("--kind", choices=["specialist", "mcp_thin", "coordinator", "guardrail"], default="specialist")
    p.add_argument("--tools", default="", help="e.g. mcp:kyc,a2a_peer:example_specialist")
    p.add_argument("--port", type=int, default=None, help="A2A port (default: next free)")
    p.add_argument("--description", default="")
    p.add_argument("--public-name", default="")
    p.add_argument("--owner", default="gernas-ai01-team")
    p.add_argument("--profile", choices=["default", "reasoning", "fast"], default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--agents-dir", default=str(ROOT / "src" / "agents"))
    p.add_argument("--tests-dir", default=str(ROOT / "tests" / "agents"))
    args = p.parse_args(argv)

    try:
        files = scaffold(args)
    except (ScaffoldError, SpecValidationError) as exc:
        print(f"[new_agent] FAILED: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        for path, text in files.items():
            print(f"===== {path} (dry run, not written) =====\n{text}")
        print("[new_agent] dry run OK: spec validates against the existing mesh.")
        return 0

    for path, text in files.items():
        if path.exists() and not args.force:
            print(f"[new_agent] FAILED: {path} exists (use --force)", file=sys.stderr)
            return 2
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"[new_agent] wrote {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")

    n = args.name
    print(f"""
[new_agent] '{n}' scaffolded and validated. Next steps:
  1. Edit src/agents/{n}/prompt.md: replace every TODO (ROLE, SCOPE, TOOLS, OPERATING RULES).
  2. Review src/agents/{n}/agent.spec.yaml: trim mcp `allow` lists to the minimum (least privilege),
     set description, model profile, max_tool_calls and guardrails.
     Agent-only Python tools go in src/agents/{n}/agent.py (then list them in the spec).
  3. If another agent should delegate to it, add to that agent's spec:
       - {{type: a2a_peer, node: {n}}}
  4. Run the tests:        .venv\\Scripts\\python -m pytest tests/agents/test_{n}.py -q
  5. Run it:               python a2a_server.py --agent {n}   (or python launch_mesh.py)
No registry edits needed: node list, ports and MCP wiring are derived from the spec.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
