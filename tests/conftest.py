"""Offline test configuration: no network, no real LLM, no developer .env.

Environment is pinned *before* ``src`` is imported because ``Config`` reads env
at import time. Runtime artefacts go to a per-session temp directory.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="istr_tests_"))
os.environ.update({
    "ISTR_SKIP_DOTENV": "1",
    "LLM_PROVIDER": "stub",
    "OBS_PROFILE": "off",
    "OPIK_ENABLED": "false",
    "ENABLE_SENSITIVE_DATA": "false",
    "AUDIT_LOG_FILE": str(_TMP / "audit.jsonl"),
    "CONVERSATION_STORE_DIR": str(_TMP / "conversations"),
    "LOG_FILE": str(_TMP / "istr.log"),
})
for k in list(os.environ):
    if k.startswith("PORT_") or k.endswith(("_PROVIDER", "_MODEL", "_API_KEY")) and k != "LLM_PROVIDER":
        os.environ.pop(k, None)

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src.factory.agent_factory import AgentFactory  # noqa: E402
from tests.fakes import ScriptedChatClient  # noqa: E402


@pytest.fixture(scope="session")
def tmp_root() -> pathlib.Path:
    return _TMP


@pytest.fixture()
def audit_path(tmp_path) -> pathlib.Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture()
def factory() -> AgentFactory:
    """Factory over the real src/agents/ specs with a fake chat client (no network, no keys)."""
    return AgentFactory(chat_client_factory=lambda spec: ScriptedChatClient(["ok"]))


@pytest.fixture()
def agents_copy(tmp_path) -> pathlib.Path:
    """A writable copy of src/agents/ for scaffolder / loader-mutation tests."""
    import shutil

    dst = tmp_path / "agents"
    shutil.copytree(ROOT / "src" / "agents", dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst
