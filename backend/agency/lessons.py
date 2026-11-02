"""Optimizer memory: error fingerprints → verified git-diff patches.

When QA fails, the optimizer fingerprints each error and asks this store for patches that
fixed the same (or a similar) error before. The best few are rendered into a markdown
block that is appended to Claude Code's system prompt for the next build. When a later QA
run passes, the diff between the failing and passing commits is stored as a verified fix
for every error it resolved, and every injected lesson is scored on whether its error
actually went away.

Tables (``data/lessons.sqlite``):

    error_fingerprints  one row per distinct normalized error (+ FTS5 index for fuzzy match)
    patches             one row per distinct diff (secrets redacted, noise paths excluded)
    fixes               fingerprint × patch: the "lesson", with verified/helped/failed counts
    verifications       which job/commit verified a fix (makes recording idempotent)
    fix_uses            each injection of a lesson into a build, and its outcome
    sightings           each time an error was observed (frequency analytics)

All writes are idempotent so a node re-executed after a crash can't double-count.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agency import db
from agency.schemas import BuildResult, ErrorCategory, ObservedError, QAReport

DEFAULT_LESSONS_PATH = Path("data") / "lessons.sqlite"
SCHEMA_VERSION = 1

MAX_DIFF_CHARS = 40_000  # bigger diffs are rewrites, not lessons
RECENCY_HALF_LIFE_DAYS = 120
QUARANTINE_MIN_USES = 4  # after this many injections...
QUARANTINE_MAX_EFFICACY = 0.25  # ...a lesson that rarely helps stops being suggested
MATCH_WEIGHT = {"exact": 1.0, "signature": 0.6, "similar": 0.35}

# --------------------------------------------------------------------------- #
# Fingerprinting
# --------------------------------------------------------------------------- #

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(
    r"(?:[A-Za-z]:[/\\])?(?:[\w@.~-]+[/\\])*[\w@.-]+\.(tsx?|jsx?|mjs|cjs|css|scss|html|json|vue|svelte|astro|py|md)\b"
)
_POSITION = re.compile(r"\(\d+,\d+\)|:\d+(?::\d+)?\b|\bline \d+(?:, column \d+)?|^\s*\d+:\d+\s+", re.MULTILINE)
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-f]{7,40}\b")
_NUMBER = re.compile(r"(?<![A-Za-z_\d])\d+(?:\.\d+)?")
_TS_CODE = re.compile(r"\bTS\d{4,5}\b")
_JS_ERROR = re.compile(r"\b(?:Uncaught\s+)?([A-Z]\w*Error)\b:?\s*([^'\"(]*)")
_LINT_RULE = re.compile(r"\s(@?[a-z][\w-]*(?:/[\w-]+)*)\s*$")


def normalize(message: str) -> str:
    """Strip the parts of an error that vary between runs/sites (paths, positions, hashes, numbers)."""
    s = _ANSI.sub("", message).strip()
    s = _URL.sub("<url>", s)
    s = _PATH.sub(lambda m: f"<file>.{m.group(1)}", s)
    s = _POSITION.sub("", s)
    s = _HEX.sub("<hex>", s)
    s = _NUMBER.sub("<n>", s)
    return re.sub(r"\s+", " ", s).strip()[:1000]


def signature(category: ErrorCategory, message: str) -> str:
    """Coarse class of an error, used when no exact fingerprint match exists."""
    if m := _TS_CODE.search(message):
        return f"{category.value}:{m.group(0)}"
    if category is ErrorCategory.LINT and (m := _LINT_RULE.search(message)):
        return f"lint:{m.group(1)}"
    if m := _JS_ERROR.search(message):
        words = " ".join(m.group(2).lower().split()[:6])
        return f"{category.value}:{m.group(1)}:{words}".rstrip(":")
    words = " ".join(normalize(message).lower().split()[:6])
    return f"{category.value}:{words}"


def observe(category: ErrorCategory, message: str) -> ObservedError:
    message = redact(message)[:4000]
    norm = normalize(message)
    fp = hashlib.sha1(f"{category.value}\x00{norm}".encode()).hexdigest()[:16]
    return ObservedError(category=category, message=message, fingerprint=fp, signature=signature(category, message)[:200])


def extract_errors(build: BuildResult | None, report: QAReport | None, min_lighthouse: int) -> list[ObservedError]:
    """Every specific, fingerprintable failure in a build + QA run (generic failures are skipped)."""
    errors: list[ObservedError] = []
    if build and not build.success:
        errors += [observe(ErrorCategory.BUILD, e) for e in build.error_logs]
    if report:
        errors += [observe(ErrorCategory.LINT, e) for e in report.lint_errors]
        errors += [observe(ErrorCategory.CONSOLE, e) for e in report.console_errors]
        if report.build_pass and report.lighthouse_score < min_lighthouse:
            errors.append(observe(ErrorCategory.LIGHTHOUSE, "Lighthouse performance score below threshold"))
    unique: dict[str, ObservedError] = {}
    for e in errors:
        unique.setdefault(e.fingerprint, e)
    return list(unique.values())[:20]


# --------------------------------------------------------------------------- #
# Redaction: lessons end up in prompts and on disk, secrets must not.
# --------------------------------------------------------------------------- #

_SECRETS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), "<redacted:private-key>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<redacted:aws-key>"),
    (re.compile(r"\b(?:sk|pk|rk)[-_](?:live|test|proj|ant)?[-_]?[A-Za-z0-9_-]{16,}"), "<redacted:api-key>"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}"), "<redacted:github-token>"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "<redacted:slack-token>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "<redacted:jwt>"),
    (
        re.compile(r"(?i)\b([\w-]*(?:api[_-]?key|secret|token|password|passwd|pwd)[\w-]*)(\s*[:=]\s*)(['\"]?)[^\s'\"]{6,}\3"),
        r"\1\2\3<redacted>\3",
    ),
]


def redact(text: str) -> str:
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


def diff_files(diff: str) -> list[str]:
    return sorted(set(re.findall(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE)))


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS error_fingerprints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    signature   TEXT NOT NULL,
    category    TEXT NOT NULL,
    normalized  TEXT NOT NULL,
    example     TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fp_signature ON error_fingerprints (signature);
CREATE VIRTUAL TABLE IF NOT EXISTS error_fts USING fts5(
    normalized, content='error_fingerprints', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS error_fts_ai AFTER INSERT ON error_fingerprints BEGIN
    INSERT INTO error_fts (rowid, normalized) VALUES (new.id, new.normalized);
END;
CREATE TRIGGER IF NOT EXISTS error_fts_ad AFTER DELETE ON error_fingerprints BEGIN
    INSERT INTO error_fts (error_fts, rowid, normalized) VALUES ('delete', old.id, old.normalized);
END;

CREATE TABLE IF NOT EXISTS patches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    diff_hash     TEXT NOT NULL UNIQUE,
    diff          TEXT NOT NULL,
    files         TEXT NOT NULL,
    additions     INTEGER NOT NULL,
    deletions     INTEGER NOT NULL,
    summary       TEXT,
    source_job    TEXT,
    source_commit TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fixes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint_id    INTEGER NOT NULL REFERENCES error_fingerprints(id),
    patch_id          INTEGER NOT NULL REFERENCES patches(id),
    verified_count    INTEGER NOT NULL DEFAULT 0,
    helped            INTEGER NOT NULL DEFAULT 0,
    failed            INTEGER NOT NULL DEFAULT 0,
    first_verified_at TEXT NOT NULL,
    last_verified_at  TEXT NOT NULL,
    last_used_at      TEXT,
    UNIQUE (fingerprint_id, patch_id)
);

CREATE TABLE IF NOT EXISTS verifications (
    fix_id     INTEGER NOT NULL REFERENCES fixes(id),
    job_id     TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    at         TEXT NOT NULL,
    PRIMARY KEY (fix_id, job_id, commit_sha)
);

CREATE TABLE IF NOT EXISTS fix_uses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fix_id            INTEGER NOT NULL REFERENCES fixes(id),
    job_id            TEXT NOT NULL,
    attempt           INTEGER NOT NULL,
    error_fingerprint TEXT NOT NULL,
    match_kind        TEXT NOT NULL,
    outcome           TEXT NOT NULL DEFAULT 'pending',
    created_at        TEXT NOT NULL,
    resolved_at       TEXT,
    UNIQUE (fix_id, job_id, attempt, error_fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_fix_uses_job ON fix_uses (job_id, outcome);

CREATE TABLE IF NOT EXISTS sightings (
    fingerprint_id INTEGER NOT NULL REFERENCES error_fingerprints(id),
    job_id         TEXT NOT NULL,
    attempt        INTEGER NOT NULL,
    at             TEXT NOT NULL,
    PRIMARY KEY (fingerprint_id, job_id, attempt)
);
"""


@dataclass
class Lesson:
    fix_id: int
    patch_id: int
    match_kind: str  # exact | signature | similar
    error: ObservedError  # the current error this lesson was matched for
    past_error: str  # example of the error the patch originally fixed
    signature: str
    diff: str
    files: list[str]
    summary: str | None
    verified_count: int
    helped: int
    failed: int
    last_verified_at: str
    score: float

    @property
    def track_record(self) -> str:
        uses = self.helped + self.failed
        return f"verified {self.verified_count}x" + (f", helped {self.helped}/{uses} reuses" if uses else "")


def _efficacy(verified: int, helped: int, failed: int) -> float:
    """Laplace-smoothed success rate; verified fixes count as successes."""
    return (verified + helped + 1) / (verified + helped + failed + 2)


def _recency(last_verified_at: str) -> float:
    age_days = (db.utcnow() - datetime.fromisoformat(last_verified_at)).total_seconds() / 86400
    return 0.5 ** (max(age_days, 0) / RECENCY_HALF_LIFE_DAYS)


def _fts_query(text: str) -> str | None:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text) if w.lower() not in {"error", "file", "hex"}]
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(words[:12])) or None


class LessonStore:
    def __init__(self, path: str | Path = DEFAULT_LESSONS_PATH) -> None:
        self.path = Path(path)
        db.init(self.path, _SCHEMA)
        with db.connect(self.path) as conn:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # ------------------------------------------------------------------ writes

    def _upsert_fingerprint(self, conn: Any, err: ObservedError, now: str) -> int:
        conn.execute(
            "INSERT INTO error_fingerprints (fingerprint, signature, category, normalized, example, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(fingerprint) DO UPDATE SET last_seen = excluded.last_seen",
            (err.fingerprint, err.signature, err.category.value, normalize(err.message), err.message[:2000], now, now),
        )
        return conn.execute("SELECT id FROM error_fingerprints WHERE fingerprint = ?", (err.fingerprint,)).fetchone()["id"]

    def observe_errors(self, job_id: str, attempt: int, errors: list[ObservedError]) -> None:
        now = db.ts(db.utcnow())
        with db.connect(self.path) as conn:
            for err in errors:
                fid = self._upsert_fingerprint(conn, err, now)
                conn.execute("INSERT OR IGNORE INTO sightings VALUES (?, ?, ?, ?)", (fid, job_id, attempt, now))

    def record_fix(
        self,
        errors: list[ObservedError],
        diff: str,
        *,
        job_id: str,
        commit: str,
        summary: str | None = None,
    ) -> list[int]:
        """Store ``diff`` as a verified fix for each error. Returns fix ids (empty if the diff is unusable)."""
        diff = redact(diff).strip()
        if not diff or len(diff) > MAX_DIFF_CHARS or not errors:
            return []
        lines = diff.splitlines()
        adds = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
        dels = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
        diff_hash = hashlib.sha256(diff.encode()).hexdigest()
        now = db.ts(db.utcnow())
        fix_ids: list[int] = []
        with db.connect(self.path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO patches (diff_hash, diff, files, additions, deletions, summary, source_job,"
                " source_commit, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (diff_hash, diff, json.dumps(diff_files(diff)), adds, dels, summary, job_id, commit, now),
            )
            patch_id = conn.execute("SELECT id FROM patches WHERE diff_hash = ?", (diff_hash,)).fetchone()["id"]
            for err in errors:
                fid = self._upsert_fingerprint(conn, err, now)
                conn.execute(
                    "INSERT OR IGNORE INTO fixes (fingerprint_id, patch_id, first_verified_at, last_verified_at)"
                    " VALUES (?, ?, ?, ?)",
                    (fid, patch_id, now, now),
                )
                fix_id = conn.execute(
                    "SELECT id FROM fixes WHERE fingerprint_id = ? AND patch_id = ?", (fid, patch_id)
                ).fetchone()["id"]
                fresh = conn.execute(
                    "INSERT OR IGNORE INTO verifications VALUES (?, ?, ?, ?)", (fix_id, job_id, commit, now)
                ).rowcount
                if fresh:
                    conn.execute(
                        "UPDATE fixes SET verified_count = verified_count + 1, last_verified_at = ? WHERE id = ?",
                        (now, fix_id),
                    )
                fix_ids.append(fix_id)
        return fix_ids

    def record_uses(self, job_id: str, attempt: int, lessons: list[Lesson]) -> None:
        now = db.ts(db.utcnow())
        with db.connect(self.path) as conn:
            for lesson in lessons:
                conn.execute(
                    "INSERT OR IGNORE INTO fix_uses (fix_id, job_id, attempt, error_fingerprint, match_kind, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (lesson.fix_id, job_id, attempt, lesson.error.fingerprint, lesson.match_kind, now),
                )
                conn.execute("UPDATE fixes SET last_used_at = ? WHERE id = ?", (now, lesson.fix_id))

    def resolve_uses(self, job_id: str, still_failing: set[str], *, can_judge_resolved: bool = True) -> tuple[int, int]:
        """Score pending injections for a job: helped if its error is gone, failed if it persists.

        With ``can_judge_resolved=False`` (e.g. the build broke, so lint/console checks never
        ran) an absent error proves nothing: only persisting errors are scored.
        """
        now = db.ts(db.utcnow())
        helped = failed = 0
        with db.connect(self.path) as conn:
            pending = conn.execute(
                "SELECT id, fix_id, error_fingerprint FROM fix_uses WHERE job_id = ? AND outcome = 'pending'", (job_id,)
            ).fetchall()
            for use in pending:
                ok = use["error_fingerprint"] not in still_failing
                if ok and not can_judge_resolved:
                    continue
                conn.execute(
                    "UPDATE fix_uses SET outcome = ?, resolved_at = ? WHERE id = ? AND outcome = 'pending'",
                    ("helped" if ok else "failed", now, use["id"]),
                )
                conn.execute(
                    f"UPDATE fixes SET {'helped = helped' if ok else 'failed = failed'} + 1 WHERE id = ?",
                    (use["fix_id"],),
                )
                helped, failed = helped + ok, failed + (not ok)
        return helped, failed

    # ------------------------------------------------------------------ reads

    def find(self, errors: list[ObservedError], *, limit: int = 4, per_error: int = 3) -> list[Lesson]:
        """Best past fixes for these errors: exact fingerprint > same signature > similar text."""
        found: dict[int, Lesson] = {}  # patch_id -> best lesson
        with db.connect(self.path) as conn:
            for err in errors:
                candidates: list[tuple[str, Any]] = []
                for kind, where, arg in (
                    ("exact", "fp.fingerprint = ?", err.fingerprint),
                    ("signature", "fp.signature = ? AND fp.fingerprint != ?", (err.signature, err.fingerprint)),
                ):
                    params = arg if isinstance(arg, tuple) else (arg,)
                    candidates += [(kind, r) for r in conn.execute(_FIX_SELECT + f" WHERE {where}", params).fetchall()]
                if query := _fts_query(normalize(err.message)):
                    rows = conn.execute(
                        _FIX_SELECT + " WHERE fp.id IN (SELECT rowid FROM error_fts WHERE error_fts MATCH ?"
                        " ORDER BY rank LIMIT 10) AND fp.category = ? AND fp.signature != ?",
                        (query, err.category.value, err.signature),
                    ).fetchall()
                    candidates += [("similar", r) for r in rows]

                scored: list[Lesson] = []
                for kind, row in candidates:
                    uses = row["helped"] + row["failed"]
                    efficacy = _efficacy(row["verified_count"], row["helped"], row["failed"])
                    if uses >= QUARANTINE_MIN_USES and efficacy < QUARANTINE_MAX_EFFICACY:
                        continue
                    scored.append(
                        Lesson(
                            fix_id=row["fix_id"], patch_id=row["patch_id"], match_kind=kind, error=err,
                            past_error=row["example"], signature=row["signature"], diff=row["diff"],
                            files=json.loads(row["files"]), summary=row["summary"],
                            verified_count=row["verified_count"], helped=row["helped"], failed=row["failed"],
                            last_verified_at=row["last_verified_at"],
                            score=MATCH_WEIGHT[kind] * efficacy * _recency(row["last_verified_at"]),
                        )
                    )
                for lesson in sorted(scored, key=lambda x: x.score, reverse=True)[:per_error]:
                    best = found.get(lesson.patch_id)
                    if best is None or lesson.score > best.score:
                        found[lesson.patch_id] = lesson
        return sorted(found.values(), key=lambda x: x.score, reverse=True)[:limit]

    def top(self, limit: int = 20, search: str | None = None) -> list[dict[str, Any]]:
        """Most valuable stored lessons, for the CLI/API."""
        sql = (
            "SELECT f.id AS fix_id, fp.signature, fp.example, p.files, p.additions, p.deletions, p.summary,"
            " f.verified_count, f.helped, f.failed, f.last_verified_at,"
            " (SELECT COUNT(*) FROM sightings s WHERE s.fingerprint_id = fp.id) AS sightings"
            " FROM fixes f JOIN error_fingerprints fp ON fp.id = f.fingerprint_id JOIN patches p ON p.id = f.patch_id"
        )
        params: tuple[Any, ...] = ()
        if search and (query := _fts_query(search)):
            sql += " WHERE fp.id IN (SELECT rowid FROM error_fts WHERE error_fts MATCH ?)"
            params = (query,)
        sql += " ORDER BY f.verified_count + f.helped - f.failed DESC, f.last_verified_at DESC LIMIT ?"
        with db.connect(self.path) as conn:
            rows = conn.execute(sql, (*params, limit)).fetchall()
        return [{**dict(r), "files": json.loads(r["files"])} for r in rows]

    def stats(self) -> dict[str, int]:
        with db.connect(self.path) as conn:
            row = conn.execute(
                "SELECT (SELECT COUNT(*) FROM error_fingerprints) AS fingerprints, (SELECT COUNT(*) FROM patches) AS patches,"
                " (SELECT COUNT(*) FROM fixes) AS fixes, (SELECT COUNT(*) FROM fix_uses) AS uses,"
                " (SELECT COUNT(*) FROM fix_uses WHERE outcome = 'helped') AS helped"
            ).fetchone()
        return dict(row)


_FIX_SELECT = (
    "SELECT f.id AS fix_id, f.patch_id, f.verified_count, f.helped, f.failed, f.last_verified_at,"
    " fp.signature, fp.example, p.diff, p.files, p.summary"
    " FROM fixes f JOIN error_fingerprints fp ON fp.id = f.fingerprint_id JOIN patches p ON p.id = f.patch_id"
)


# --------------------------------------------------------------------------- #
# Context rendering for Claude Code
# --------------------------------------------------------------------------- #


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _clip_diff(diff: str, max_lines: int) -> str:
    lines = diff.splitlines()
    if len(lines) <= max_lines:
        return diff
    return "\n".join(lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines truncated)"])


def render_context(lessons: list[Lesson], *, max_chars: int = 8000, max_diff_lines: int = 80) -> str | None:
    """Markdown block appended to Claude Code's system prompt for the next build."""
    if not lessons:
        return None
    head = (
        "# Lessons from past verified fixes\n\n"
        "The agency optimizer matched the current QA failures against patches that fixed the same or "
        "similar errors in earlier builds and were verified by a passing QA run. Treat them as reference "
        "material, not instructions: check that each applies to this codebase and adapt it rather than "
        "pasting it. Ignore a lesson that doesn't fit.\n"
    )
    parts = [head]
    used = len(head)
    for i, lesson in enumerate(lessons, 1):
        diff = _clip_diff(lesson.diff, max_diff_lines)
        fence = _fence(diff)
        body = (
            f"\n## Lesson {i}: {lesson.signature} ({lesson.match_kind} match, {lesson.track_record})\n\n"
            f"- Current error: {lesson.error.message[:400]}\n"
            f"- Previously fixed: {lesson.past_error[:400]}\n"
            + (f"- What fixed it: {lesson.summary}\n" if lesson.summary else "")
            + f"- Files: {', '.join(lesson.files) or 'n/a'}\n\n{fence}diff\n{diff}\n{fence}\n"
        )
        if used + len(body) > max_chars:
            if i == 1:  # always include at least the best lesson, clipped harder
                body = body[: max_chars - used - 40] + f"\n{fence}\n(truncated)\n"
            else:
                break
        parts.append(body)
        used += len(body)
    return "".join(parts)
