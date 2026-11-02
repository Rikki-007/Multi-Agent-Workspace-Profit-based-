"""Operations shared by the HTTP API and the CLI: submit jobs and human answers.

Both only enqueue work and broadcast an event; the worker does the graph work.
"""

from __future__ import annotations

import uuid
from typing import Any

from agency.events import EventStore, summarize
from agency.jobqueue import JobQueue, WorkKind
from agency.schemas import Brief, JobState, LeadRecord

APPROVAL = "compliance_approval"
CLIENT_REPLY = "client_reply"


class JobExists(Exception):
    pass


class NotWaiting(Exception):
    pass


def new_job_id() -> str:
    return f"job-{uuid.uuid4().hex[:8]}"


def submit_job(
    queue: JobQueue,
    store: EventStore,
    *,
    job_id: str | None = None,
    lead: LeadRecord | None = None,
    brief: Brief | None = None,
) -> str:
    job_id = job_id or new_job_id()
    if store.job(job_id) is not None:
        raise JobExists(job_id)
    payload: dict[str, Any] = {}
    if lead:
        payload["lead"] = lead.model_dump(mode="json")
    if brief:
        payload["brief"] = brief.model_dump(mode="json")
    queue.enqueue(job_id, WorkKind.START, payload)
    name = lead.business_name if lead else "auto-discovered lead"
    state = JobState(job_id=job_id, lead=lead, brief=brief)
    store.record(job_id, "queued", f"job queued ({name}{f', brief {brief.code}' if brief else ''})", summary=summarize(job_id, state))
    return job_id


def submit_answer(
    queue: JobQueue,
    store: EventStore,
    job_id: str,
    expect: str,
    payload: dict[str, Any],
    *,
    strict: bool = True,
) -> int:
    """Queue a human/client answer for the interrupt ``expect``.

    With ``strict`` the job must currently be waiting on that interrupt and not
    already have an answer queued (stops double-clicks from queuing twice).
    """
    summary = store.job(job_id)
    if strict:
        if summary is None:
            raise KeyError(job_id)
        if summary.get("waiting_on") != expect:
            raise NotWaiting(f"job {job_id} is not waiting on {expect}")
        if summary.get("answer_queued"):
            raise NotWaiting(f"an answer for job {job_id} is already queued")
    item_id = queue.enqueue(job_id, WorkKind.RESUME, payload, expect=expect)
    store.patch(job_id, "answer_queued", _describe(expect, payload), answer_queued=expect)
    return item_id


def _describe(expect: str, payload: dict[str, Any]) -> str:
    if expect == APPROVAL:
        verdict = "approved" if payload.get("approved") else "rejected"
    else:
        verdict = "accepted" if payload.get("accepted") else "declined"
    note = payload.get("notes") or payload.get("message")
    return f"{expect.replace('_', ' ')}: {verdict} (queued)" + (f" — {note}" if note else "")
