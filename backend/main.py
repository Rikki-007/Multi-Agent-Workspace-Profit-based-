"""Entry point for the agency.

    python main.py                           # dashboard + API + worker (same as `serve`)
    python main.py serve [--port 8000] [--no-worker] [worker options]
    python main.py run [worker options]      # headless worker only
    python main.py start [--job-id ID]       # enqueue a new job
    python main.py approve ID [--notes ...]  # enqueue an answer to the approval gate
    python main.py reject ID [--notes ...]
    python main.py reply ID --accept|--decline [--message ...]   # enqueue the client's reply
    python main.py status ID                 # graph state + queue history for one job
    python main.py queue                     # recent queue items
    python main.py demo                      # whole lifecycle in-process, no worker needed

    python main.py lessons [--search TEXT]   # optimizer memory: verified fixes

Worker options: --concurrency N, --discover-every SECONDS, --stub-delay SECONDS,
--failure-rate 0..1 (the last two make stub runs slow/flaky to exercise the dashboard),
--claude-code [--workspaces DIR --claude-model M --claude-budget USD] to build with
Claude Code in a git repo per job instead of the stub.

Commands other than serve/run/demo only write to the queue; the worker executes them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from langgraph.types import Command

from agency import graph as agency_graph
from agency.events import EventStore
from agency.graph import DEFAULT_DB_PATH, compile_graph, job_config, open_checkpointer
from agency.claude_code import ClaudeCodeRunner
from agency.jobqueue import DEFAULT_QUEUE_PATH, JobQueue
from agency.lessons import DEFAULT_LESSONS_PATH, LessonStore
from agency.schemas import JobState
from agency.service import APPROVAL, CLIENT_REPLY, JobExists, new_job_id, submit_answer, submit_job
from agency.worker import Worker, WorkerConfig, configure_logging


def _print_status(job_id: str, db: str, queue: JobQueue | None = None) -> None:
    with open_checkpointer(db) as saver:
        snapshot = compile_graph(saver).get_state(job_config(job_id))
    if not snapshot.values:
        print(f"[{job_id}] no checkpoint yet")
    else:
        state = JobState.model_validate(snapshot.values)
        print(f"[{job_id}] stage={state.stage.value} next={list(snapshot.next) or '-'}")
        for intr in snapshot.interrupts:
            print("  waiting on:", json.dumps(intr.value, indent=2, default=str).replace("\n", "\n  "))
        if not snapshot.next:
            summary: dict[str, Any] = {
                "lead_status": state.lead.status.value if state.lead else None,
                "compliance": state.compliance_decision.value,
                "commit": state.build_result.commit_hash if state.build_result else None,
                "lighthouse": state.qa_report.lighthouse_score if state.qa_report else None,
                "preview_url": state.preview_url,
                "client_response": state.client_response,
            }
            print("  result:", json.dumps(summary, indent=2).replace("\n", "\n  "))
            print("  audit log:")
            for e in state.audit_log:
                print(f"    {e.at:%H:%M:%S} {e.node:<16} {e.message}")
    if queue:
        items = queue.list(job_id)
        if items:
            print("  queue:")
            for it in items:
                _print_item(it, indent="    ")


def _print_item(it: Any, indent: str = "") -> None:
    label = it.kind.value + (f"({it.expect})" if it.expect else "")
    err = f"  - {it.last_error}" if it.last_error else ""
    print(f"{indent}#{it.id:<4} {it.job_id:<14} {label:<28} {it.status.value:<8} tries={it.attempts}{err}")


def _demo(db: str) -> None:
    job_id = new_job_id()
    steps: list[tuple[str, JobState | Command]] = [
        ("start empty job", JobState(job_id=job_id)),
        ("(new process) human approves", Command(resume={"approved": True, "notes": "demo approval"})),
        ("(days later) client replies", Command(resume={"accepted": True, "message": "Looks great, let's go."})),
    ]
    for i, (title, payload) in enumerate(steps, 1):
        print(f"{'' if i == 1 else chr(10)}== {i}. {title} ==")
        with open_checkpointer(db) as saver:
            compile_graph(saver).invoke(payload, job_config(job_id))
        _print_status(job_id, db)


def _worker_config(args: argparse.Namespace) -> WorkerConfig:
    agency_graph.STUB.delay = args.stub_delay
    agency_graph.STUB.failure_rate = args.failure_rate
    claude = None
    if args.claude_code:
        claude = ClaudeCodeRunner(model=args.claude_model, max_budget_usd=args.claude_budget)
    return WorkerConfig(
        checkpoint_db=Path(args.db),
        queue_db=Path(args.queue_db),
        lessons_db=Path(args.lessons_db),
        claude=claude,
        workspaces_root=Path(args.workspaces) if args.claude_code else None,
        concurrency=args.concurrency,
        poll_interval=args.poll,
        max_idle_interval=args.max_idle,
        discover_every=args.discover_every,
        lease=timedelta(minutes=args.lease_minutes),
    )


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from server import create_app

    configure_logging(Path(args.log_dir))
    app = create_app(Path(args.queue_db), None if args.no_worker else _worker_config(args), Path(args.lessons_db))
    print(f"Dashboard: http://{args.host}:{args.port}/", flush=True)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_config=None,  # use our logging setup
        timeout_graceful_shutdown=5,  # don't wait forever on open SSE streams
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite checkpoint file")
    p.add_argument("--queue-db", default=str(DEFAULT_QUEUE_PATH), help="SQLite queue/events file")
    p.add_argument("--lessons-db", default=str(DEFAULT_LESSONS_PATH), help="SQLite optimizer lessons file")
    sub = p.add_subparsers(dest="cmd")

    worker_opts = argparse.ArgumentParser(add_help=False)
    worker_opts.add_argument("--concurrency", type=int, default=2)
    worker_opts.add_argument("--poll", type=float, default=2.0, help="busy poll interval (s)")
    worker_opts.add_argument("--max-idle", type=float, default=30.0, help="max idle poll interval (s)")
    worker_opts.add_argument("--discover-every", type=float, default=None, help="enqueue a new job every N seconds")
    worker_opts.add_argument("--lease-minutes", type=float, default=10.0)
    worker_opts.add_argument("--stub-delay", type=float, default=0.0, help="seconds each stub node sleeps")
    worker_opts.add_argument("--failure-rate", type=float, default=0.0, help="chance a stub build/QA run fails")
    worker_opts.add_argument("--log-dir", default="logs")
    worker_opts.add_argument("--claude-code", action="store_true", help="build with `claude -p` instead of the stub")
    worker_opts.add_argument("--workspaces", default="data/workspaces", help="git repo per job (with --claude-code)")
    worker_opts.add_argument("--claude-model", default=None)
    worker_opts.add_argument("--claude-budget", type=float, default=2.0, help="max USD per build run")

    serve = sub.add_parser("serve", parents=[worker_opts], help="dashboard + API (+ embedded worker)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-worker", action="store_true", help="API only; run the worker separately")
    sub.add_parser("run", parents=[worker_opts], help="headless background worker")

    sub.add_parser("demo")
    s = sub.add_parser("start")
    s.add_argument("--job-id", default=None)
    for name in ("approve", "reject"):
        a = sub.add_parser(name)
        a.add_argument("job_id")
        a.add_argument("--notes", default=None)
    r = sub.add_parser("reply")
    r.add_argument("job_id")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--accept", action="store_true")
    g.add_argument("--decline", action="store_true")
    r.add_argument("--message", default=None)
    st = sub.add_parser("status")
    st.add_argument("job_id")
    q = sub.add_parser("queue")
    q.add_argument("--limit", type=int, default=30)
    ls = sub.add_parser("lessons", help="show the optimizer's verified fixes")
    ls.add_argument("--search", default=None)
    ls.add_argument("--limit", type=int, default=20)
    return p


COMMANDS = {"serve", "run", "demo", "start", "approve", "reject", "reply", "status", "queue", "lessons"}
GLOBAL_FLAGS = {"--db", "--queue-db", "--lessons-db"}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not COMMANDS.intersection(argv) and not {"-h", "--help"}.intersection(argv):
        # Bare `python main.py [serve flags]` boots dashboard + worker; keep global flags in front.
        i = 0
        while i < len(argv) and argv[i].split("=")[0] in GLOBAL_FLAGS:
            i += 1 if "=" in argv[i] else 2
        argv = [*argv[:i], "serve", *argv[i:]]
    args = build_parser().parse_args(argv)

    if args.cmd == "serve":
        _serve(args)
        return
    if args.cmd == "run":
        configure_logging(Path(args.log_dir))
        asyncio.run(Worker(_worker_config(args)).run())
        return
    if args.cmd == "demo":
        _demo(args.db)
        return

    queue, store = JobQueue(args.queue_db), EventStore(args.queue_db)
    if args.cmd == "start":
        try:
            job_id = submit_job(queue, store, job_id=args.job_id)
        except JobExists:
            sys.exit(f"job {args.job_id} already exists")
        print(f"queued: start {job_id}")
    elif args.cmd in ("approve", "reject"):
        payload = {"approved": args.cmd == "approve", "notes": args.notes}
        item = submit_answer(queue, store, args.job_id, APPROVAL, payload, strict=False)
        print(f"queued #{item}: {args.cmd} {args.job_id}")
    elif args.cmd == "reply":
        payload = {"accepted": args.accept, "message": args.message}
        item = submit_answer(queue, store, args.job_id, CLIENT_REPLY, payload, strict=False)
        print(f"queued #{item}: client reply for {args.job_id}")
    elif args.cmd == "status":
        _print_status(args.job_id, args.db, queue)
    elif args.cmd == "queue":
        for it in queue.list(limit=args.limit):
            _print_item(it)
    elif args.cmd == "lessons":
        lessons = LessonStore(args.lessons_db)
        print(json.dumps(lessons.stats()))
        for row in lessons.top(limit=args.limit, search=args.search):
            print(
                f"#{row['fix_id']:<4} {row['signature'][:48]:<48} verified={row['verified_count']}"
                f" helped={row['helped']} failed={row['failed']} seen={row['sightings']}"
                f" +{row['additions']}/-{row['deletions']} {', '.join(row['files'])[:60]}"
            )


if __name__ == "__main__":
    main()
