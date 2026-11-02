"""FastAPI backend for the live dashboard.

    GET  /                               dashboard (templates/index.html)
    GET  /api/jobs                       current card for every active + recently finished job
    GET  /api/jobs/{id}                  one job: card, event history, queue items
    POST /api/jobs                       start a job (optionally seeded with a lead)
    POST /api/leads                      ingest a batch of leads, one job each
    POST /api/jobs/{id}/approval         human decision for the compliance gate
    POST /api/jobs/{id}/client-reply     record the client's answer at closing
    GET  /api/events                     Server-Sent Events: every job change, live
    GET  /api/lessons                    optimizer memory: verified fixes, best first
    GET  /api/health                     worker liveness
    POST /api/intake/web                 PUBLIC: a client's project brief -> intake record (pending review)
    GET  /api/intakes                    intake queue for human review (?status=pending_review)
    POST /api/intakes/{id}/route         accept an intake: create its pipeline job
    POST /api/intakes/{id}/reject        decline an intake

The API only enqueues work. The background worker (embedded here by default, or run
separately with ``python main.py run``) executes it and records events, which
``/api/events`` tails from SQLite. That keeps the stream working across processes and
lets a reconnecting browser resume from ``Last-Event-ID``.

Run with ``python main.py`` (server + worker) or ``uvicorn server:app``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from agency import db
from agency.events import EventStore
from agency.intake import IntakeError, IntakeSource, IntakeStatus, IntakeStore, to_json
from agency.jobqueue import DEFAULT_QUEUE_PATH, JobQueue
from agency.lessons import DEFAULT_LESSONS_PATH, LessonStore
from agency.schemas import Brief, EntityType, LeadRecord
from agency.service import APPROVAL, CLIENT_REPLY, JobExists, NotWaiting, submit_answer, submit_job
from agency.worker import Worker, WorkerConfig

log = logging.getLogger("agency.server")

INDEX_HTML = Path(__file__).parent / "templates" / "index.html"
JOB_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
SSE_POLL_SECONDS = 0.5
SSE_REPLAY = 300  # events replayed to a fresh (non-resuming) client
WORKER_ONLINE_WINDOW = timedelta(seconds=20)


# Public intake: the only route meant to face the internet. Everything else here is the operator
# API and must stay behind localhost or auth.
INTAKE_ORIGINS = {
    o.strip()
    for o in os.environ.get("AGENCY_INTAKE_ORIGINS", "https://www.monolythzro.com,https://monolythzro.com").split(",")
    if o.strip()
}
INTAKE_MAX_BYTES = 16_384
INTAKE_RATE = (5, 60.0)  # requests per window (seconds) per client IP


class RateLimiter:
    """In-memory sliding window; enough for one process (put a shared limiter in front if you scale out)."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self.hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


def _cors(origin: str | None) -> dict[str, str]:
    if origin not in INTAKE_ORIGINS:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


class IntakeDecision(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)


class NewJob(BaseModel):
    job_id: str | None = Field(default=None, pattern=JOB_ID_PATTERN)
    lead: LeadRecord | None = None


class ApprovalIn(BaseModel):
    approved: bool
    notes: str | None = Field(default=None, max_length=2000)


class ClientReplyIn(BaseModel):
    accepted: bool
    message: str | None = Field(default=None, max_length=5000)


def _workers_payload(store: EventStore) -> dict[str, Any]:
    now = db.utcnow()
    workers = store.workers()
    for w in workers:
        w["online"] = now - datetime.fromisoformat(w["last_seen"]) < WORKER_ONLINE_WINDOW
    return {"workers": workers, "online": any(w["online"] for w in workers)}


def create_app(
    queue_db: Path = DEFAULT_QUEUE_PATH,
    worker_config: WorkerConfig | None = None,
    lessons_db: Path = DEFAULT_LESSONS_PATH,
) -> FastAPI:
    """Build the app. With ``worker_config`` the background worker runs inside the server's event loop."""
    queue = JobQueue(queue_db)
    store = EventStore(queue_db)
    lessons = LessonStore(worker_config.lessons_db if worker_config else lessons_db)
    intakes = IntakeStore(queue_db)
    intake_limiter = RateLimiter(*INTAKE_RATE)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        worker = Worker(worker_config, handle_signals=False) if worker_config else None
        task = asyncio.create_task(worker.run(), name="worker") if worker else None
        if task:
            task.add_done_callback(_log_worker_exit)
        try:
            yield
        finally:
            if worker and task:
                worker.stop.set()
                await asyncio.wait({task}, timeout=worker_config.shutdown_grace + 5)

    app = FastAPI(title="Agency Control", lifespan=lifespan)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})

    # ---------------------------------------------------------------- intake (public)

    @app.options("/api/intake/web", include_in_schema=False)
    def intake_preflight(origin: str | None = Header(default=None)) -> Response:
        headers = _cors(origin)
        return Response(status_code=204 if headers else 403, headers=headers)

    @app.post("/api/intake/web", status_code=201)
    async def intake_web(request: Request) -> JSONResponse:
        headers = _cors(request.headers.get("origin"))

        def reply(status: int, body: dict[str, Any]) -> JSONResponse:
            return JSONResponse(body, status_code=status, headers=headers)

        if not intake_limiter.allow(request.client.host if request.client else "unknown"):
            return reply(429, {"detail": "Too many submissions. Please try again in a minute."})
        raw = await request.body()
        if len(raw) > INTAKE_MAX_BYTES:
            return reply(413, {"detail": "Brief too large."})
        try:
            payload = json.loads(raw or b"null")
        except json.JSONDecodeError:
            return reply(422, {"detail": "Body must be JSON."})
        if isinstance(payload, dict) and payload.get("website"):  # honeypot: only bots fill it
            return reply(201, {"intake_id": "int_received", "brief_code": str(payload.get("brief_code", "")), "status": "pending_review"})
        try:
            intake, created = await asyncio.to_thread(intakes.receive, payload, IntakeSource.WEB)
        except IntakeError as exc:
            return reply(422, {"detail": str(exc)})
        if created:
            await asyncio.to_thread(
                store.record, None, "intake", f"intake {intake.intake_id}: brief {intake.brief_code} via web (pending review)"
            )
        return reply(201 if created else 200, {"intake_id": intake.intake_id, "brief_code": intake.brief_code, "status": intake.status.value})

    # ---------------------------------------------------------------- intake review (operator)

    @app.get("/api/intakes")
    def list_intakes(status: IntakeStatus | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return [to_json(i) for i in intakes.list(status, min(limit, 500))]

    @app.post("/api/intakes/{intake_id}/route", status_code=201)
    def route_intake(intake_id: str, body: IntakeDecision | None = None) -> dict[str, Any]:
        intake = intakes.get(intake_id)
        if intake is None:
            raise HTTPException(404, f"unknown intake {intake_id}")
        if intake.status is not IntakeStatus.PENDING_REVIEW:
            raise HTTPException(409, f"intake {intake_id} is already {intake.status.value}")
        c = intake.contact
        lead = LeadRecord(
            business_name=c.company or c.name or intake.brief_code,
            entity_type=EntityType.UNKNOWN,  # the compliance gate verifies it
            email=c.email,
            phone=c.phone,
            source=f"intake:{intake.intake_id}",
        )
        brief = Brief(code=intake.brief_code, tier=intake.tier, style=intake.style, intake_id=intake.intake_id)
        try:
            # Deterministic id: routing the same intake twice can never create two jobs.
            job_id = submit_job(queue, store, job_id=f"job-{intake.intake_id}", lead=lead, brief=brief)
        except JobExists:
            raise HTTPException(409, f"intake {intake_id} already has a job") from None
        routed = intakes.decide(intake_id, IntakeStatus.ROUTED, job_id=job_id, notes=body.notes if body else None)
        return to_json(routed)

    @app.post("/api/intakes/{intake_id}/reject")
    def reject_intake(intake_id: str, body: IntakeDecision | None = None) -> dict[str, Any]:
        try:
            return to_json(intakes.decide(intake_id, IntakeStatus.REJECTED, notes=body.notes if body else None))
        except KeyError:
            raise HTTPException(404, f"unknown intake {intake_id}") from None
        except IntakeError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, **_workers_payload(store)}

    @app.get("/api/lessons")
    def list_lessons(search: str | None = None, limit: int = 50) -> dict[str, Any]:
        return {"stats": lessons.stats(), "lessons": lessons.top(limit=min(limit, 200), search=search)}

    @app.get("/api/jobs")
    def list_jobs(done_limit: int = 50) -> list[dict[str, Any]]:
        return store.jobs(done_limit=min(done_limit, 500))

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        summary = store.job(job_id)
        if summary is None:
            raise HTTPException(404, f"unknown job {job_id}")
        return {
            "job": summary,
            "events": store.recent_events(job_id),
            "queue": [i.model_dump(mode="json") for i in queue.list(job_id)],
        }

    @app.post("/api/jobs", status_code=201)
    def create_job(body: NewJob | None = None) -> dict[str, str]:
        body = body or NewJob()
        try:
            job_id = submit_job(queue, store, job_id=body.job_id, lead=body.lead)
        except JobExists:
            raise HTTPException(409, f"job {body.job_id} already exists") from None
        return {"job_id": job_id}

    @app.post("/api/leads", status_code=201)
    def ingest_leads(leads: list[LeadRecord]) -> dict[str, list[str]]:
        if not 1 <= len(leads) <= 200:
            raise HTTPException(422, "send between 1 and 200 leads")
        return {"job_ids": [submit_job(queue, store, lead=lead) for lead in leads]}

    @app.post("/api/jobs/{job_id}/approval", status_code=202)
    def approve(job_id: str, body: ApprovalIn) -> dict[str, Any]:
        return _answer(job_id, APPROVAL, body.model_dump())

    @app.post("/api/jobs/{job_id}/client-reply", status_code=202)
    def client_reply(job_id: str, body: ClientReplyIn) -> dict[str, Any]:
        return _answer(job_id, CLIENT_REPLY, body.model_dump())

    def _answer(job_id: str, expect: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            item_id = submit_answer(queue, store, job_id, expect, payload)
        except KeyError:
            raise HTTPException(404, f"unknown job {job_id}") from None
        except NotWaiting as exc:
            raise HTTPException(409, str(exc)) from None
        return {"queued": item_id, "job_id": job_id}

    @app.get("/api/events")
    async def events(
        request: Request,
        after: int | None = None,
        last_event_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        if last_event_id and last_event_id.isdigit():
            cursor = int(last_event_id)  # browser reconnect: resume exactly where it left off
        elif after is not None:
            cursor = after
        else:
            cursor = max(0, await asyncio.to_thread(store.last_event_id) - SSE_REPLAY)

        async def stream() -> AsyncIterator[str]:
            nonlocal cursor
            yield "retry: 3000\n\n"
            last_status = 0.0
            loop = asyncio.get_running_loop()
            while not await request.is_disconnected():
                batch = await asyncio.to_thread(store.events_after, cursor, 500)
                for event in batch:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: job\ndata: {json.dumps(event)}\n\n"
                if loop.time() - last_status >= 5:  # worker liveness, doubles as keep-alive
                    status = await asyncio.to_thread(_workers_payload, store)
                    yield f"event: workers\ndata: {json.dumps(status, default=str)}\n\n"
                    last_status = loop.time()
                if len(batch) < 500:
                    await asyncio.sleep(SSE_POLL_SECONDS)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _log_worker_exit(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception():
        log.error("embedded worker crashed", exc_info=task.exception())


def __getattr__(name: str) -> Any:
    """Lazy ``app`` for ``uvicorn server:app``, so importing ``create_app`` has no side effects.

    Set AGENCY_EMBED_WORKER=0 when the worker runs as its own process.
    """
    if name == "app":
        embed = os.environ.get("AGENCY_EMBED_WORKER", "1") != "0"
        globals()["app"] = create_app(worker_config=WorkerConfig() if embed else None)
        return globals()["app"]
    raise AttributeError(name)
