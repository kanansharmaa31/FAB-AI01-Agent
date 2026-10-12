"""Launch the ISTR mesh: one process (and port) per registered node.

Usage:
    python launch_mesh.py                 # all nodes (start mock services yourself)
    python launch_mesh.py --with-mocks    # also start the synthetic MCP services first
    python launch_mesh.py --only example_specialist,example_coordinator

The node list and start order come from the agent specs (specialists first,
coordinator last). Nothing here needs editing when an agent is added.
"""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONWARNINGS", "ignore")

import argparse  # noqa: E402
import pathlib  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.agents.node_registry import NODE_NAMES, NODE_PORTS, start_order, workflow_node_problems  # noqa: E402
from src.config import Config  # noqa: E402
from src.mock_services.server import local_services  # noqa: E402

PORT_TIMEOUT = 45.0


def wait_for_port(host: str, port: int, timeout: float = PORT_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def start_mocks() -> subprocess.Popen | None:
    services = local_services()
    if not services:
        return None
    p = subprocess.Popen([sys.executable, "-m", "src.mock_services"], cwd=ROOT)
    for s in services:
        u = urlparse(Config.mcp_url(s))
        ok = wait_for_port(u.hostname or "127.0.0.1", u.port or 80)
        print(f"  -> mock {s:<13} {'ready' if ok else 'NOT READY'}  {Config.mcp_url(s)}")
    return p


def start_nodes(names: list[str]) -> list[tuple[str, subprocess.Popen]]:
    procs = []
    for name in names:
        port = NODE_PORTS[name]
        p = subprocess.Popen([sys.executable, str(ROOT / "a2a_server.py"), "--agent", name], cwd=ROOT)
        procs.append((name, p))
        ok = wait_for_port(Config.A2A_HOST, port)
        state = "ready" if ok else ("EXITED" if p.poll() is not None else "NOT READY")
        print(f"  -> {name:<22} pid={p.pid:<6} http://{Config.A2A_HOST}:{port}/  {state}")
    return procs


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the ISTR agent mesh.")
    parser.add_argument("--with-mocks", action="store_true", help="Start the synthetic MCP services first.")
    parser.add_argument("--only", default="", help="Comma-separated node subset.")
    args = parser.parse_args()

    only = [n.strip() for n in args.only.split(",") if n.strip()]
    bad = set(only) - set(NODE_NAMES)
    if bad:
        parser.error(f"unknown nodes: {', '.join(sorted(bad))}. Valid: {', '.join(NODE_NAMES)}")
    names = [n for n in start_order() if not only or n in only]

    print("=" * 72 + "\n  ISTR AGENT MESH (Microsoft Agent Framework + A2A)\n" + "=" * 72)
    print(f"  LLM_PROVIDER={Config.LLM_PROVIDER}  coordinator={Config.WORKFLOW_COORDINATOR_NODE}  "
          f"compliance={Config.WORKFLOW_COMPLIANCE_NODE or '(none)'}")
    for problem in workflow_node_problems():
        print(f"  WARNING: {problem}")
    mock = start_mocks() if args.with_mocks else None
    procs = start_nodes(names)
    print("-" * 72 + "\n  Ready. Try:  python run.py --case CASE-001\n  Ctrl+C stops everything.\n" + "=" * 72)

    def _shutdown(*_: object) -> None:
        print("\n[mesh] shutting down")
        for _, p in procs:
            p.terminate()
        if mock:
            mock.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    reported: set[str] = set()
    try:
        while True:
            time.sleep(1)
            for name, p in procs:
                if p.poll() is not None and name not in reported:
                    reported.add(name)
                    print(f"[mesh] WARNING: node '{name}' exited (code {p.returncode})")
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
