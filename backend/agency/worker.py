"""Long-running background worker.

Polls the ``JobQueue``, drives each claimed item through the graph, and sleeps with
backoff when idle. Designed to be kept alive by PM2 / systemd / Task Scheduler:

* Crash-safe: graph progress is checkpointed after every node, and queue items are
  leased, so a killed worker's jobs are reclaimed and continue from the last checkpoint.
* Graceful: SIGINT/SIGTERM stop new claims, let in-flight jobs finish for
  ``shutdown_grace`` seconds, then hand any unfinished items back to the queue.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import time
from dataclasses import dataclass
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from langgraph.graph.state import CompiledStateGraph
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from agency import db
from agency.events import NODE_COLUMN, EventStore, summarize
from agency.claude_code import ClaudeCodeRunner
from agency.graph import (
    DEFAULT_DB_PATH,
    AgencyContext,
    GitWorkspaces,
    compile_graph,
    job_config,
    open_async_checkpointer,
    stub_workspaces,
)
from agency.lessons import DEFAULT_LESSONS_PATH, LessonStore
from agency.jobqueue import DEFAULT_QUEUE_PATH, JobQueue, WorkItem, WorkKind, WorkStatus
from agency.schemas import AuditEvent, JobState
from agency.service import submit_job

log = logging.getLogger("agency.worker")


@dataclass
class WorkerConfig:
    checkpoint_db: Path = DEFAULT_DB_PATH
    queue_db: Path = DEFAULT_QUEUE_PATH
    lessons_db: Path = DEFAULT_LESSONS_PATH
    claude: ClaudeCodeRunner | None = None  # set to build with Claude Code instead of the stub
    workspaces_root: Path | None = None  # one git repo per job; required for Claude builds
    concurrency: int = 2
    poll_interval: float = 2.0  # seconds between polls while busy
    max_idle_interval: float = 30.0  # polling backs off to this while the queue is empty
    lease: timedelta = timedelta(minutes=10)
    max_attempts: int = 5
    retry_base: timedelta = timedelta(seconds=30)
    discover_every: float | None = None  # seconds; None disables the discovery scheduler
    shutdown_grace: float = 30.0
    heartbeat_every: float = 5.0  # seconds between liveness pings shown on the dashboard


def configure_logging(log_dir: Path = Path("logs"), level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / "agency.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    stream_handler = logging.StreamHandler()
    for h in (file_handler, stream_handler):
        h.setFormatter(fmt)
    logging.basicConfig(level=level, handlers=[file_handler, stream_handler], force=True)


class Worker:
    def __init__(self, config: WorkerConfig, *, handle_signals: bool = True) -> None:
        self.config = config
        self.queue = JobQueue(config.queue_db)
        self.store = EventStore(config.queue_db)
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.handle_signals = handle_signals  # off when embedded in uvicorn, which owns the signals
        self.stop = asyncio.Event()
        self._graph: CompiledStateGraph | None = None
        self._started_at = db.ts(db.utcnow())
        self.context = AgencyContext(
            lessons=LessonStore(config.lessons_db),
            workspaces=(
                GitWorkspaces(config.workspaces_root, install=config.claude is not None)
                if config.workspaces_root
                else stub_workspaces
            ),
            claude=config.claude,
        )

    # ------------------------------------------------------------------ main loop

    async def run(self) -> None:
        cfg = self.config
        if self.handle_signals:
            self._install_signal_handlers()
        log.info(
            "worker %s starting (concurrency=%d, checkpoints=%s, queue=%s)",
            self.worker_id, cfg.concurrency, cfg.checkpoint_db, cfg.queue_db,
        )

        async with open_async_checkpointer(cfg.checkpoint_db) as saver:
            self._graph = compile_graph(saver)
            inflight: set[asyncio.Task[None]] = set()
            stop_waiter = asyncio.create_task(self.stop.wait())
            discovery = asyncio.create_task(self._discovery_loop()) if cfg.discover_every else None
            idle = cfg.poll_interval
            last_beat = 0.0

            while not self.stop.is_set():
                if time.monotonic() - last_beat >= cfg.heartbeat_every:
                    await asyncio.to_thread(self.store.beat, self.worker_id, self._started_at, len(inflight))
                    last_beat = time.monotonic()
                claimed = 0
                while len(inflight) < cfg.concurrency and not self.stop.is_set():
                    item = await asyncio.to_thread(self.queue.claim, self.worker_id, cfg.lease)
                    if item is None:
                        break
                    claimed += 1
                    task = asyncio.create_task(self._process(item), name=f"item-{item.id}")
                    inflight.add(task)
                    task.add_done_callback(inflight.discard)

                idle = cfg.poll_interval if claimed else min(idle * 2, cfg.max_idle_interval)
                # Wake on shutdown, on a free slot, or when the poll interval elapses.
                await asyncio.wait({stop_waiter, *inflight}, timeout=idle, return_when=asyncio.FIRST_COMPLETED)

            log.info("shutdown requested; waiting up to %.0fs for %d in-flight item(s)", cfg.shutdown_grace, len(inflight))
            if discovery:
                discovery.cancel()
            if inflight:
                _, pending = await asyncio.wait(inflight, timeout=cfg.shutdown_grace)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.to_thread(self.store.worker_stopped, self.worker_id)
        log.info("worker %s stopped", self.worker_id)

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        sigs = [signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)]
        for sig in filter(None, sigs):
            try:
                loop.add_signal_handler(sig, self.stop.set)
            except NotImplementedError:  # Windows event loops
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(self.stop.set))

    # ------------------------------------------------------------------ items

    async def _process(self, item: WorkItem) -> None:
        cfg = self.config
        if item.attempts > cfg.max_attempts:
            log.error("item %d (job %s) exceeded %d attempts; giving up", item.id, item.job_id, cfg.max_attempts)
            await asyncio.to_thread(self.queue.finish, item.id, WorkStatus.FAILED, item.last_error)
            await self._error(item.job_id, f"gave up after {cfg.max_attempts} attempts: {item.last_error}")
            return

        log.info("item %d: %s job %s (attempt %d)", item.id, item.kind.value, item.job_id, item.attempts)
        heartbeat = asyncio.create_task(self._heartbeat(item))
        try:
            status, note = await self._execute(item)
            await self._sync(item.job_id)
            await asyncio.to_thread(self.queue.finish, item.id, status, note)
            if status is WorkStatus.SKIPPED:
                log.warning("item %d skipped: %s", item.id, note)
                await asyncio.to_thread(
                    self.store.patch, item.job_id, "skipped", f"{item.kind.value} skipped: {note}",
                    level="warn", answer_queued=None,
                )
        except asyncio.CancelledError:
            await asyncio.to_thread(self.queue.release, item.id)
            log.warning("item %d interrupted by shutdown; released back to queue", item.id)
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if item.attempts >= cfg.max_attempts:
                log.exception("item %d failed permanently", item.id)
                await asyncio.to_thread(self.queue.finish, item.id, WorkStatus.FAILED, error)
                await self._error(item.job_id, f"failed permanently after {item.attempts} attempts: {error}")
            else:
                delay = min(cfg.retry_base * 2 ** (item.attempts - 1), timedelta(hours=1))
                log.exception("item %d failed; retrying in %s", item.id, delay)
                await asyncio.to_thread(self.queue.retry_later, item.id, delay, error)
                await self._error(item.job_id, f"attempt {item.attempts} crashed: {error}; retrying in {delay}")
        finally:
            heartbeat.cancel()

    async def _execute(self, item: WorkItem) -> tuple[WorkStatus, str | None]:
        assert self._graph is not None
        graph, config = self._graph, job_config(item.job_id)
        snap = await graph.aget_state(config)
        crashed_mid_run = bool(snap.next) and not snap.interrupts

        if item.kind is WorkKind.START:
            if not snap.values:
                state = JobState.model_validate({**item.payload, "job_id": item.job_id})
                await self._drive(item.job_id, state, config)
            elif crashed_mid_run:
                log.info("job %s: continuing from last checkpoint", item.job_id)
                await self._drive(item.job_id, None, config)
            else:
                return WorkStatus.SKIPPED, "job already started"
        else:
            if crashed_mid_run:
                log.info("job %s: continuing from last checkpoint before resuming", item.job_id)
                await self._drive(item.job_id, None, config)
                snap = await graph.aget_state(config)
            # Only deliver an answer to the question it was meant for, so a stale
            # "approve" can never be read as a client reply.
            if any(i.value.get("kind") == item.expect for i in snap.interrupts):
                await self._drive(item.job_id, Command(resume=item.payload), config)
            elif crashed_mid_run:
                # A previous attempt delivered the answer and then crashed downstream.
                return WorkStatus.DONE, "answer already applied; continued from checkpoint"
            else:
                return WorkStatus.SKIPPED, f"job is not waiting on {item.expect!r}"

        return WorkStatus.DONE, None

    async def _drive(self, job_id: str, graph_input: JobState | Command | None, config: RunnableConfig) -> None:
        """Run the graph and publish every node start, node result and interrupt as it happens."""
        assert self._graph is not None
        values: dict[str, object] | None = None
        node: str | None = None
        waiting: dict[str, object] | None = None
        pending: list[AuditEvent] = []

        async for mode, chunk in self._graph.astream(
            graph_input, config, context=self.context, stream_mode=["tasks", "updates", "values"]
        ):
            if mode == "tasks":
                if "input" in chunk and chunk["name"] in NODE_COLUMN:  # a node is starting
                    node, waiting = chunk["name"], None
                    summary = summarize(job_id, values, node=node, running=True)
                    await asyncio.to_thread(
                        self.store.record, job_id, "node_started", f"{node} running", node=node, summary=summary
                    )
            elif mode == "updates":
                for name, update in chunk.items():
                    if name == "__interrupt__":
                        waiting = update[0].value
                    elif isinstance(update, dict):
                        pending.extend(update.get("audit_log", []))
            else:  # values: state after a step; publish what that step produced
                values = dict(chunk)
                if interrupts := values.pop("__interrupt__", None):
                    waiting = interrupts[0].value
                summary = summarize(job_id, values, node=node, waiting=waiting)
                for ev in pending:
                    await asyncio.to_thread(
                        self.store.record, job_id, "node", ev.message, node=ev.node, level=ev.level, summary=summary
                    )
                if waiting and not pending:
                    await asyncio.to_thread(
                        self.store.record, job_id, "waiting", f"waiting on {waiting.get('kind')}",
                        node=node, level="warn", summary=summary,
                    )
                pending = []

    async def _error(self, job_id: str, message: str) -> None:
        await asyncio.to_thread(
            self.store.patch, job_id, "error", message, level="error", last_error=message, running=False
        )

    async def _sync(self, job_id: str) -> None:
        """Make the dashboard card match the checkpoint, whatever happened during the run."""
        assert self._graph is not None
        snap = await self._graph.aget_state(job_config(job_id))
        if not snap.values:
            return
        waiting = snap.interrupts[0].value if snap.interrupts else None
        summary = summarize(job_id, snap.values, node=snap.next[0] if snap.next else None, waiting=waiting)
        stored = await asyncio.to_thread(self.store.job, job_id)
        if stored is None or any(stored.get(k) != summary[k] for k in ("column", "stage", "waiting_on", "running")):
            await asyncio.to_thread(self.store.record, job_id, "sync", "card resynced from checkpoint", summary=summary)

        if waiting:
            # TODO: push to email/Slack instead of only logging.
            log.warning("ACTION NEEDED: job %s is waiting on %s", job_id, waiting.get("kind"))
        elif not snap.next:
            log.info("job %s finished with stage=%s", job_id, summary["stage"])

    async def _heartbeat(self, item: WorkItem) -> None:
        interval = self.config.lease.total_seconds() / 3
        while True:
            await asyncio.sleep(interval)
            await asyncio.to_thread(self.queue.renew, item.id, self.worker_id, self.config.lease)

    # ------------------------------------------------------------------ discovery

    async def _discovery_loop(self) -> None:
        """Periodically enqueue a fresh job so the pipeline keeps finding leads on its own."""
        assert self.config.discover_every
        while not self.stop.is_set():
            job_id = await asyncio.to_thread(submit_job, self.queue, self.store)
            log.info("discovery: enqueued %s", job_id)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.config.discover_every)
            except TimeoutError:
                pass
