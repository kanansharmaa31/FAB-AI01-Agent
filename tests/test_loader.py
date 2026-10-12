"""Loader: discovery, cross-spec validation (fails loudly, all errors at once), prompt rendering."""
import pathlib

import pytest
import yaml

from src.factory.loader import REQUIRED_SECTIONS, SpecLoader, SpecValidationError

SPECIALIST, COORDINATOR = "example_specialist", "example_coordinator"


def _edit_spec(agents: pathlib.Path, agent: str, **changes) -> None:
    path = agents / agent / "agent.spec.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for k, v in changes.items():
        if "." in k:
            a, b = k.split(".")
            data[a][b] = v
        else:
            data[k] = v
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_real_agents_load():
    specs = SpecLoader().specs()
    assert {SPECIALIST, COORDINATOR} <= set(specs)


def test_rendered_prompt_has_sections_shared_rules_and_reasoning():
    loader = SpecLoader()
    for name, la in loader.load().items():
        text = loader.render_instructions(la.spec, la.prompt_template)
        for section in REQUIRED_SECTIONS:
            assert f"## {section}" in text, (name, section)
        assert "ISTR-WIDE RULES" in text
        assert f'"agent":"{name}"' in text  # reasoning block self-identifies
        assert "{{" not in text, f"unresolved template variable in {name}"


def test_coordinator_prompt_embeds_istrdraft_schema():
    loader = SpecLoader()
    la = loader.get(COORDINATOR)
    text = loader.render_instructions(la.spec, la.prompt_template)
    assert "## OUTPUT SCHEMA" in text and "PENDING_MLRO_REVIEW" in text


def test_all_errors_reported_together(agents_copy):
    _edit_spec(agents_copy, SPECIALIST, **{"a2a.port": 8020})  # clash with the coordinator
    _edit_spec(agents_copy, SPECIALIST, tools=[
        {"type": "mcp", "service": "transactions", "allow": ["detect_patterns"]},
        {"type": "function", "ref": "src.tools.istr_tools:nope"},
    ])
    _edit_spec(agents_copy, SPECIALIST, guardrails={"input": ["vibes"], "output": [], "pii_redaction": True})
    _edit_spec(agents_copy, COORDINATOR, middleware=["audit", "telepathy"])
    _edit_spec(agents_copy, COORDINATOR, tools=[{"type": "a2a_peer", "node": "ghost_agent"}])
    (agents_copy / COORDINATOR / "prompt.md").unlink()
    with pytest.raises(SpecValidationError) as exc:
        SpecLoader(agents_copy).load()
    msg = str(exc.value)
    for fragment in ("port 8020 clashes", "cannot be imported", "unknown middleware 'telepathy'",
                     "unknown guardrail 'vibes'", "a2a_peer node 'ghost_agent' does not exist",
                     "instructions_file not found"):
        assert fragment in msg, fragment
    assert len(exc.value.errors) >= 6


def test_missing_prompt_section_and_unresolved_var(agents_copy):
    p = agents_copy / SPECIALIST / "prompt.md"
    p.write_text(p.read_text(encoding="utf-8").replace("## OUTPUT FORMAT", "## OUTPUT") + "\n{{ nonexistent }}",
                 encoding="utf-8")
    with pytest.raises(SpecValidationError) as exc:
        SpecLoader(agents_copy).load()
    assert "missing prompt sections ['OUTPUT FORMAT']" in str(exc.value)
    assert "unresolved variables ['nonexistent']" in str(exc.value)


def test_directory_name_must_match_and_yaml_must_parse(agents_copy):
    _edit_spec(agents_copy, SPECIALIST, name="specialist_two")
    (agents_copy / COORDINATOR / "agent.spec.yaml").write_text("name: [unclosed", encoding="utf-8")
    with pytest.raises(SpecValidationError) as exc:
        SpecLoader(agents_copy).load()
    assert "must equal its directory name" in str(exc.value)
    assert "invalid YAML" in str(exc.value)


def test_port_env_override_clash_detected(agents_copy, monkeypatch):
    monkeypatch.setenv("PORT_EXAMPLE_SPECIALIST", "8020")  # coordinator's port
    with pytest.raises(SpecValidationError) as exc:
        SpecLoader(agents_copy).load()
    assert "port 8020 clashes" in str(exc.value)


def test_agent_py_required_with_builder(agents_copy):
    (agents_copy / SPECIALIST / "agent.py").unlink()
    p = agents_copy / COORDINATOR / "agent.py"
    p.write_text(p.read_text(encoding="utf-8").replace("def get_example_coordinator_agent", "def build_it"),
                 encoding="utf-8")
    with pytest.raises(SpecValidationError) as exc:
        SpecLoader(agents_copy).load()
    assert "missing agent entry point" in str(exc.value)
    assert "must define get_example_coordinator_agent" in str(exc.value)


def test_removing_a_peer_agent_is_reported(agents_copy):
    """Deleting an agent folder that another agent still lists as a peer fails loudly."""
    import shutil

    shutil.rmtree(agents_copy / SPECIALIST)
    with pytest.raises(SpecValidationError) as exc:
        SpecLoader(agents_copy).load()
    assert "a2a_peer node 'example_specialist' does not exist" in str(exc.value)


def test_peer_cannot_be_coordinator(agents_copy):
    _edit_spec(agents_copy, SPECIALIST, tools=[
        {"type": "mcp", "service": "transactions", "allow": ["detect_patterns"]},
        {"type": "a2a_peer", "node": COORDINATOR},
    ])
    with pytest.raises(SpecValidationError) as exc:
        SpecLoader(agents_copy).load()
    assert "is a coordinator" in str(exc.value)
