"""scripts/new_agent.py: renders, validates against the existing mesh, writes only when valid."""
import importlib.util
import pathlib

import pytest
import yaml

from src.factory.loader import SpecLoader

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("new_agent", ROOT / "scripts" / "new_agent.py")
new_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(new_agent)


def run(agents_dir, tests_dir, *argv):
    return new_agent.main([*argv, "--agents-dir", str(agents_dir), "--tests-dir", str(tests_dir)])


def test_dry_run_writes_nothing(agents_copy, tmp_path, capsys):
    rc = run(agents_copy, tmp_path / "t", "zz_scaffold_probe", "--tools", "mcp:kyc,a2a_peer:example_specialist",
             "--port", "8031", "--dry-run")
    assert rc == 0
    assert not (agents_copy / "zz_scaffold_probe").exists()
    out = capsys.readouterr().out
    assert "dry run OK" in out and "public_name: ZzScaffoldProbeAgent" in out


def test_real_scaffold_creates_valid_agent(agents_copy, tmp_path, capsys):
    tests_dir = tmp_path / "tests_agents"
    rc = run(agents_copy, tests_dir, "zz_scaffold_probe", "--kind", "specialist",
             "--tools", "mcp:kyc[get_kyc_profile|get_risk_rating],a2a_peer:example_specialist")
    assert rc == 0
    spec_file = agents_copy / "zz_scaffold_probe" / "agent.spec.yaml"
    assert spec_file.exists() and (agents_copy / "zz_scaffold_probe" / "prompt.md").exists()
    agent_py = (agents_copy / "zz_scaffold_probe" / "agent.py").read_text(encoding="utf-8")
    assert "def get_zz_scaffold_probe_agent(" in agent_py
    assert (tests_dir / "test_zz_scaffold_probe.py").exists()
    compile((tests_dir / "test_zz_scaffold_probe.py").read_text(encoding="utf-8"), "gen", "exec")

    specs = SpecLoader(agents_copy).specs()            # whole mesh still validates
    demo = specs["zz_scaffold_probe"]
    assert demo.a2a.port == max(s.a2a.port for n, s in specs.items() if n != "zz_scaffold_probe") + 1
    assert demo.mcp_tools[0].allow == ["get_kyc_profile", "get_risk_rating"]
    assert demo.peer_tools[0].node == "example_specialist"
    assert "Next steps" in capsys.readouterr().out


def test_existing_agent_needs_force(agents_copy, tmp_path):
    args = ["example_specialist", "--kind", "mcp_thin", "--tools", "mcp:screening"]
    assert run(agents_copy, tmp_path, *args) == 2
    assert run(agents_copy, tmp_path, *args, "--port", "8021", "--force") == 0
    data = yaml.safe_load((agents_copy / "example_specialist" / "agent.spec.yaml").read_text(encoding="utf-8"))
    assert data["kind"] == "mcp_thin"


@pytest.mark.parametrize("argv, fragment", [
    (["x_agent", "--kind", "mcp_thin"], "requires at least one tool of type 'mcp'"),
    (["x_agent", "--tools", "a2a_peer:ghost"], "does not exist"),
    (["x_agent", "--tools", "mcp:kyc", "--port", "8021"], "clashes"),
    (["x_agent", "--tools", "mcp:nosuch"], "unknown MCP service"),
    (["x_agent", "--tools", "mcp:kyc[drop_all]"], "has no tool"),
    (["x_agent", "--tools", "function:src.tools.istr_tools:missing"], "cannot be imported"),
    (["Bad-Name"], "snake_case"),
])
def test_invalid_requests_fail_and_write_nothing(agents_copy, tmp_path, capsys, argv, fragment):
    assert run(agents_copy, tmp_path, *argv) == 2
    assert fragment in capsys.readouterr().err
    assert not (agents_copy / "x_agent").exists()
