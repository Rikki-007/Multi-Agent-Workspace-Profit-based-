"""Shared SQLite helpers for the agency's own tables (queue, events, job index)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ts(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    """Short-lived connection with a transaction: commit on success, roll back on error.

    One connection per call keeps this safe across processes and threads.
    """
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init(path: Path, schema: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(schema)
