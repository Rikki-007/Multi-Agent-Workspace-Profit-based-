"""Durable work queue that feeds the background worker.

Anything that wants the graph to do something (the CLI, a future webhook handler,
the discovery scheduler) enqueues a ``WorkItem``; only the worker executes them.

Items are claimed with a lease. A worker that crashes or is killed mid-job simply
stops renewing its lease, and the item becomes claimable again once it expires.
Items for the same job run strictly in FIFO order, one at a time.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agency import db
from agency.db import ts as _ts
from agency.db import utcnow as _now

DEFAULT_QUEUE_PATH = Path("data") / "queue.sqlite"


class WorkKind(str, Enum):
    START = "start"  # payload: JobState fields
    RESUME = "resume"  # payload: value for Command(resume=...)


class WorkStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkItem(BaseModel):
    id: int
    job_id: str
    kind: WorkKind
    expect: str | None  # for RESUME: the interrupt kind this answers, e.g. "compliance_approval"
    payload: dict[str, Any]
    status: WorkStatus
    attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    expect       TEXT,
    payload      TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    available_at TEXT    NOT NULL,
    lease_until  TEXT,
    worker_id    TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_work_items_claim ON work_items (status, available_at);
CREATE INDEX IF NOT EXISTS ix_work_items_job ON work_items (job_id, id);
"""

# Claimable: pending and due, or running with an expired lease (its worker died).
# Never claim an item while an earlier item for the same job is still unfinished.
_CLAIM = """
UPDATE work_items
   SET status = 'running', attempts = attempts + 1, lease_until = :lease_until,
       worker_id = :worker_id, updated_at = :now
 WHERE id = (
    SELECT q.id FROM work_items q
     WHERE ((q.status = 'pending' AND q.available_at <= :now)
            OR (q.status = 'running' AND q.lease_until < :now))
       AND NOT EXISTS (
            SELECT 1 FROM work_items e
             WHERE e.job_id = q.job_id AND e.id < q.id AND e.status IN ('pending', 'running'))
     ORDER BY q.id
     LIMIT 1)
RETURNING *
"""


def _row_to_item(row: sqlite3.Row) -> WorkItem:
    return WorkItem(
        id=row["id"],
        job_id=row["job_id"],
        kind=row["kind"],
        expect=row["expect"],
        payload=json.loads(row["payload"]),
        status=row["status"],
        attempts=row["attempts"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class JobQueue:
    """Small synchronous SQLite queue. Each call uses its own short-lived connection,
    so it is safe from multiple processes and from ``asyncio.to_thread``."""

    def __init__(self, path: str | Path = DEFAULT_QUEUE_PATH) -> None:
        self.path = Path(path)
        db.init(self.path, _SCHEMA)

    def enqueue(
        self,
        job_id: str,
        kind: WorkKind,
        payload: dict[str, Any] | None = None,
        *,
        expect: str | None = None,
    ) -> int:
        now = _ts(_now())
        with db.connect(self.path) as conn:
            cur = conn.execute(
                "INSERT INTO work_items (job_id, kind, expect, payload, available_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, kind.value, expect, json.dumps(payload or {}), now, now, now),
            )
            return int(cur.lastrowid)

    def claim(self, worker_id: str, lease: timedelta) -> WorkItem | None:
        now = _now()
        with db.connect(self.path) as conn:
            row = conn.execute(
                _CLAIM, {"now": _ts(now), "lease_until": _ts(now + lease), "worker_id": worker_id}
            ).fetchone()
        return _row_to_item(row) if row else None

    def renew(self, item_id: int, worker_id: str, lease: timedelta) -> None:
        now = _now()
        with db.connect(self.path) as conn:
            conn.execute(
                "UPDATE work_items SET lease_until = ?, updated_at = ?"
                " WHERE id = ? AND worker_id = ? AND status = 'running'",
                (_ts(now + lease), _ts(now), item_id, worker_id),
            )

    def finish(self, item_id: int, status: WorkStatus, error: str | None = None) -> None:
        with db.connect(self.path) as conn:
            conn.execute(
                "UPDATE work_items SET status = ?, last_error = ?, lease_until = NULL, updated_at = ?"
                " WHERE id = ?",
                (status.value, error, _ts(_now()), item_id),
            )

    def retry_later(self, item_id: int, delay: timedelta, error: str) -> None:
        now = _now()
        with db.connect(self.path) as conn:
            conn.execute(
                "UPDATE work_items SET status = 'pending', last_error = ?, available_at = ?,"
                " lease_until = NULL, updated_at = ? WHERE id = ?",
                (error, _ts(now + delay), _ts(now), item_id),
            )

    def release(self, item_id: int) -> None:
        """Hand an interrupted item back without counting the attempt (graceful shutdown)."""
        now = _ts(_now())
        with db.connect(self.path) as conn:
            conn.execute(
                "UPDATE work_items SET status = 'pending', attempts = MAX(attempts - 1, 0),"
                " lease_until = NULL, available_at = ?, updated_at = ? WHERE id = ? AND status = 'running'",
                (now, now, item_id),
            )

    def list(self, job_id: str | None = None, limit: int = 50) -> list[WorkItem]:
        sql = "SELECT * FROM work_items"
        params: tuple[Any, ...] = ()
        if job_id:
            sql += " WHERE job_id = ?"
            params = (job_id,)
        sql += " ORDER BY id DESC LIMIT ?"
        with db.connect(self.path) as conn:
            rows = conn.execute(sql, (*params, limit)).fetchall()
        return [_row_to_item(r) for r in reversed(rows)]
