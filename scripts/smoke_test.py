"""End-to-end smoke test with real processes and NO API key.

Starts the five synthetic MCP services and every registered agent as separate A2A
server processes (stub LLM), runs sample case 1 (or ``--all``) through the real
workflow over real A2A + MCP (streamable HTTP on localhost), checks the draft,
then tears everything down.

    python scripts/smoke_test.py            # CASE-001
    python scripts/smoke_test.py --all      # CASE-001..003
"""
from __future__ import annotations

import os

# Pin an offline configuration BEFORE src is imported (Config reads env at import).
os.environ.update({"ISTR_SKIP_DOTENV": "1", "LLM_PROVIDER": "stub", "OBS_PROFILE": "off",
                   "OPIK_ENABLED": "false", "PYTHONWARNINGS": "ignore"})

import argparse  # noqa: E402
import asyncio  # noqa: E402
import pathlib  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from src.agents.node_registry import NODE_PORTS, start_order  # noqa: E402
from src.auth.identity_provider import login  # noqa: E402
from src.config import Config  # noqa: E402
from src.mesh.orchestrator import handle_request  # noqa: E402
from src.mock_services.data_store import load  # noqa: E402

EXPECTED = {c["case_id"]: c["expected_outcome"] for c in load("cases")["cases"]}


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def wait_health(url: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="run all three sample cases")
    args = ap.parse_args()
    cases = sorted(EXPECTED) if args.all else ["CASE-001"]

    mcp_ports = [(urlparse(u).hostname, urlparse(u).port) for u in Config.MCP_SERVICE_URLS.values()]
    busy = [p for h, p in mcp_ports if port_open(h, p)] + \
           [p for p in NODE_PORTS.values() if port_open(Config.A2A_HOST, p)]
    if busy:
        print(f"[smoke] ports already in use: {busy}. Stop the running mesh first.")
        return 2

    logdir = pathlib.Path(tempfile.mkdtemp(prefix="istr_smoke_"))
    env = {**os.environ, "AUDIT_LOG_FILE": str(logdir / "audit.jsonl"), "LOG_FILE": str(logdir / "mesh.log"),
           "CONVERSATION_STORE_DIR": str(logdir / "conversations")}
    os.environ.update({k: env[k] for k in ("AUDIT_LOG_FILE", "LOG_FILE", "CONVERSATION_STORE_DIR")})
    procs: list[tuple[str, subprocess.Popen, pathlib.Path]] = []

    def spawn(label: str, argv: list[str]) -> None:
        log = logdir / f"{label}.log"
        p = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=log.open("w"), stderr=subprocess.STDOUT)
        procs.append((label, p, log))

    ok = False
    try:
        t0 = time.perf_counter()
        spawn("mock_services", [sys.executable, "-m", "src.mock_services"])
        for h, p in mcp_ports:
            deadline = time.monotonic() + 30
            while not port_open(h, p) and time.monotonic() < deadline:
                time.sleep(0.2)
        print(f"[smoke] mock MCP services up ({len(mcp_ports)})")
        for node in start_order():
            spawn(node, [sys.executable, str(ROOT / "a2a_server.py"), "--agent", node])
        for node in start_order():
            if not wait_health(f"http://{Config.A2A_HOST}:{NODE_PORTS[node]}/health"):
                raise RuntimeError(f"node '{node}' did not become healthy")
        print(f"[smoke] {len(NODE_PORTS)} A2A nodes healthy in {time.perf_counter() - t0:.1f}s")

        failures = []
        for case_id in cases:
            t1 = time.perf_counter()
            r = asyncio.run(handle_request(login("analyst1"), case_id=case_id))
            d = r.draft or {}
            checks = {
                "not blocked": not r.blocked,
                "status PENDING_MLRO_REVIEW": r.status == d.get("status") == "PENDING_MLRO_REVIEW",
                f"recommendation == {EXPECTED[case_id]}": d.get("recommendation") == EXPECTED[case_id],
                "findings all cited": bool(d.get("findings")) and all(f["citations"] for f in d["findings"]),
                "no missing evidence": d.get("missing_evidence") == [],
                "no review issues": d.get("review_issues") == [],
                "narrative drafted": d.get("narrative", "").startswith("DRAFT NARRATIVE"),
            }
            status = "PASS" if all(checks.values()) else "FAIL"
            print(f"[smoke] {case_id}: {status} in {time.perf_counter() - t1:.1f}s  "
                  f"recommendation={d.get('recommendation')} findings={len(d.get('findings', []))} "
                  f"agents={sorted({e['agent'] for e in r.reasoning})}")
            for name, passed in checks.items():
                if not passed:
                    failures.append(f"{case_id}: {name}")
                    print(f"         x {name}")
            if case_id == cases[0]:
                print("         summary:", r.summary.splitlines()[0][:160])
        audit_lines = (logdir / "audit.jsonl").read_text(encoding="utf-8").count("\n") \
            if (logdir / "audit.jsonl").exists() else 0
        print(f"[smoke] audit records written: {audit_lines} ({logdir / 'audit.jsonl'})")
        ok = not failures
    except Exception as exc:
        print(f"[smoke] ERROR: {exc}")
    finally:
        for label, p, log in procs:
            p.terminate()
        for label, p, log in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
            if not ok:
                tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
                print(f"--- {label} log tail ---\n" + "\n".join(tail))
    print(f"[smoke] {'PASSED' if ok else 'FAILED'} (logs: {logdir})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
