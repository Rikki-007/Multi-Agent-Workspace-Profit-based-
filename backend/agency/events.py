"""Event log and job index that power the live dashboard.

The worker records an event every time a job changes (node started, node finished,
interrupt, error). Each event can carry a *summary*: a small JSON view of the job used
to draw its card. The latest summary per job is also kept in the ``jobs`` table so the
dashboard can load the board without replaying history.

Everything lives in SQLite, so the API server and the worker can run in the same
process or in separate ones.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from agency import db
from agency.jobqueue import DEFAULT_QUEUE_PATH
from agency.schemas import JobStage, JobState

# Dashboard columns, in board order. "done" holds finished jobs.
COLUMNS = ("discovered", "compliance", "building", "qa", "preview", "closing", "done")

NODE_COLUMN = {
    "lead_discovery": "discovered",
    "compliance_check": "compliance",
    "approval_gate": "compliance",
    "strategy": "building",
    "build": "building",
    "optimizer": "building",
    "qa": "qa",
    "preview_deploy": "preview",
    "closing": "closing",
}
WAITING_COLUMN = {"compliance_approval": "compliance", "client_reply": "closing"}
TERMINAL_STAGES = {JobStage.COMPLETED, JobStage.FAILED, JobStage.ABANDONED}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id     TEXT PRIMARY KEY,
    col        TEXT NOT NULL,
    summary    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_col ON jobs (col, updated_at);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    job_id  TEXT,
    kind    TEXT NOT NULL,
    node    TEXT,
    level   TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    summary TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_job ON events (job_id, id);
CREATE TABLE IF NOT EXISTS workers (
    worker_id  TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    inflight   INTEGER NOT NULL DEFAULT 0
);
"""


def summarize(
    job_id: str,
    values: JobState | dict[str, Any] | None,
    *,
    node: str | None = None,
    running: bool = False,
    waiting: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact, JSON-safe view of a job for a dashboard card."""
    state = values if isinstance(values, JobState) else JobState.model_validate(values or {"job_id": job_id})
    waiting_on = waiting.get("kind") if waiting else None

    if state.stage in TERMINAL_STAGES and not running:
        column = "done"
    elif waiting_on:
        column = WAITING_COLUMN.get(waiting_on, "compliance")
    else:
        column = NODE_COLUMN.get(node or "", "discovered")

    return {
        "job_id": job_id,
        "column": column,
        "stage": state.stage.value,
        "node": node,
        "running": running,
        "waiting_on": waiting_on,
        "lead": state.lead.model_dump(mode="json") if state.lead else None,
        "compliance_flags": state.compliance_flags,
        "compliance_decision": state.compliance_decision.value,
        "reviewer_notes": state.reviewer_notes,
        "build_attempts": state.build_attempts,
        "max_build_attempts": state.max_build_attempts,
        "build": state.build_result.model_dump(mode="json") if state.build_result else None,
        "qa": state.qa_report.model_dump(mode="json") if state.qa_report else None,
        "optimizer_feedback": state.optimizer_feedback,
        "lessons_injected": len(state.injected_lesson_ids),
        "lessons_recorded": state.lessons_recorded,
        "preview_url": state.preview_url,
        "client_response": state.client_response,
        "last_error": None,
        "answer_queued": None,
    }


def _event_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "at": row["at"],
        "job_id": row["job_id"],
        "kind": row["kind"],
        "node": row["node"],
        "level": row["level"],
        "message": row["message"],
        "summary": json.loads(row["summary"]) if row["summary"] else None,
    }


class EventStore:
    def __init__(self, path: str | Path = DEFAULT_QUEUE_PATH) -> None:
        self.path = Path(path)
        db.init(self.path, _SCHEMA)

    # ------------------------------------------------------------------ writes

    def record(
        self,
        job_id: str | None,
        kind: str,
        message: str,
        *,
        node: str | None = None,
        level: str = "info",
        summary: dict[str, Any] | None = None,
    ) -> int:
        """Append an event; if it carries a summary, also make that the job's current card."""
        now = db.ts(db.utcnow())
        with db.connect(self.path) as conn:
            if summary is not None and job_id:
                summary = {**summary, "updated_at": now}
                conn.execute(
                    "INSERT INTO jobs (job_id, col, summary, created_at, updated_at) VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(job_id) DO UPDATE SET col = excluded.col, summary = excluded.summary,"
                    " updated_at = excluded.updated_at",
                    (job_id, summary["column"], json.dumps(summary), now, now),
                )
            cur = conn.execute(
                "INSERT INTO events (at, job_id, kind, node, level, message, summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now, job_id, kind, node, level, message, json.dumps(summary) if summary is not None else None),
            )
            return int(cur.lastrowid)

    def patch(self, job_id: str, kind: str, message: str, *, level: str = "info", **fields: Any) -> dict[str, Any] | None:
        """Change fields on a job's current card (e.g. ``last_error``) and broadcast it."""
        summary = self.job(job_id)
        if summary is None:
            self.record(job_id, kind, message, level=level)
            return None
        summary.update(fields)
        self.record(job_id, kind, message, level=level, node=summary.get("node"), summary=summary)
        return summary

    def beat(self, worker_id: str, started_at: str, inflight: int) -> None:
        now = db.ts(db.utcnow())
        with db.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO workers (worker_id, started_at, last_seen, inflight) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(worker_id) DO UPDATE SET last_seen = excluded.last_seen, inflight = excluded.inflight",
                (worker_id, started_at, now, inflight),
            )
            # Forget workers that have been silent for a day.
            conn.execute("DELETE FROM workers WHERE last_seen < ?", (db.ts(db.utcnow() - timedelta(days=1)),))

    def worker_stopped(self, worker_id: str) -> None:
        with db.connect(self.path) as conn:
            conn.execute("DELETE FROM workers WHERE worker_id = ?", (worker_id,))

    # ------------------------------------------------------------------ reads

    def job(self, job_id: str) -> dict[str, Any] | None:
        with db.connect(self.path) as conn:
            row = conn.execute("SELECT summary FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return json.loads(row["summary"]) if row else None

    def jobs(self, done_limit: int = 50) -> list[dict[str, Any]]:
        """Every unfinished job plus the most recently finished ones."""
        with db.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT summary FROM jobs WHERE col != 'done'"
                " UNION ALL SELECT summary FROM (SELECT summary FROM jobs WHERE col = 'done'"
                " ORDER BY updated_at DESC LIMIT ?)",
                (done_limit,),
            ).fetchall()
        return [json.loads(r["summary"]) for r in rows]

    def events_after(self, after_id: int, limit: int = 500, job_id: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM events WHERE id > ?", [after_id]
        if job_id:
            sql += " AND job_id = ?"
            params.append(job_id)
        with db.connect(self.path) as conn:
            rows = conn.execute(sql + " ORDER BY id LIMIT ?", (*params, limit)).fetchall()
        return [_event_row(r) for r in rows]

    def recent_events(self, job_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with db.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE job_id = ? ORDER BY id DESC LIMIT ?", (job_id, limit)
            ).fetchall()
        return [_event_row(r) for r in reversed(rows)]

    def last_event_id(self) -> int:
        with db.connect(self.path) as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM events").fetchone()
        return int(row["m"])

    def workers(self) -> list[dict[str, Any]]:
        with db.connect(self.path) as conn:
            rows = conn.execute("SELECT * FROM workers ORDER BY started_at").fetchall()
        return [dict(r) for r in rows]
