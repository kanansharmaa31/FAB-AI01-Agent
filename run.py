"""CLI client: run one case/query through the running mesh.

Usage:
    python run.py --case CASE-001
    python run.py --case CASE-002 --user investigator1 --query "Focus on the PEP linkage"
    python run.py --case CASE-001 --stream          # print stage events live
    python run.py --case CASE-001 --json            # full machine-readable result
"""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONWARNINGS", "ignore")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from src.auth.identity_provider import login  # noqa: E402
from src.mesh.orchestrator import InvestigationResult, handle_request_stream  # noqa: E402
from src.observability import setup_observability  # noqa: E402


def render(result: InvestigationResult) -> str:
    lines = ["=" * 72, f" {result.case_id or '-'}  status={result.status}  request={result.request_id}", "=" * 72]
    if result.blocked:
        lines += [f"BLOCKED at {result.block_stage}: {result.summary}"]
        return "\n".join(lines)
    lines += [result.summary, ""]
    d = result.draft or {}
    lines += [f"Recommendation for MLRO: {d.get('recommendation')}  ({d.get('rationale', '')})",
              f"Typologies: {', '.join(d.get('typologies', [])) or '-'}", "", "Findings:"]
    for f in d.get("findings", []):
        refs = ", ".join(c["record_id"] for c in f.get("citations", [])[:5])
        lines.append(f"  {f.get('finding_id', '-')} [{f.get('severity')}] {f.get('title')}: "
                     f"{f.get('description', '')[:140]}  [{refs}]")
    if d.get("missing_evidence"):
        lines += ["", "Missing evidence:"] + [f"  - {m}" for m in d["missing_evidence"]]
    if d.get("review_issues"):
        lines += ["", "Review issues:"] + [f"  - {i}" for i in d["review_issues"]]
    lines += ["", f"Narrative: {d.get('narrative', '')[:600]}", "", f"Trail: {' -> '.join(result.trail)}",
              f"Agents with reasoning: {sorted({r.get('agent') for r in result.reasoning})}",
              "", d.get("disclaimer", "")]
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> InvestigationResult:
    q: asyncio.Queue = asyncio.Queue()

    async def _printer() -> None:
        while (ev := await q.get()) is not None:
            if args.stream and ev.get("event_type") == "stage":
                print(f"  [{ev['stage']:<18}] {ev['status']:<15} {ev.get('message', '')}")
            elif args.stream and ev.get("event_type") == "reasoning":
                for e in ev["entries"]:
                    print(f"  [reasoning:{e['agent']}] {e['phase']}")

    printer = asyncio.create_task(_printer())
    result = await handle_request_stream(login(args.user), args.query, case_id=args.case,
                                         session_id=args.session, event_queue=q)
    await printer
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Run an ISTR investigation through the mesh.")
    p.add_argument("--case", default="CASE-001")
    p.add_argument("--user", default="analyst1", help="demo users: analyst1, investigator1, mlro1, rm1 (blocked)")
    p.add_argument("--query", default="")
    p.add_argument("--session", default=None)
    p.add_argument("--stream", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    setup_observability(service_name="istr_client")
    result = asyncio.run(_main(args))
    print(json.dumps(result.to_dict(), indent=2) if args.json else render(result))
    sys.exit(1 if result.status not in ("PENDING_MLRO_REVIEW", "BLOCKED") else 0)


if __name__ == "__main__":
    main()
