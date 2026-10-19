import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import DATABASE_PATH


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


_initialized_dbs: set[str] = set()


def initialize_storage() -> None:
    db_key = str(DATABASE_PATH)
    if db_key in _initialized_dbs:
        return
    with _connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL;")
        connection.execute("PRAGMA synchronous = NORMAL;")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS passports (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                github_username TEXT NOT NULL,
                target_role TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                step TEXT NOT NULL,
                completed_steps TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                passport_id TEXT,
                target TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        try:
            connection.execute("ALTER TABLE jobs ADD COLUMN target TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            connection.execute("ALTER TABLE jobs ADD COLUMN updated_at TEXT")
        except sqlite3.OperationalError:
            pass
        # Students asking for a human look at an excluded citation. Keyed by passport id and
        # citation key, so requests survive re-checks; they never change levels or exclusions.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS review_requests (
                id TEXT PRIMARY KEY,
                passport_id TEXT NOT NULL,
                citation_key TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                reviewed_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limits (
                key TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_limits_key_ts ON rate_limits (key, timestamp)"
        )
    _initialized_dbs.add(db_key)


def save_passport(passport: dict[str, Any]) -> None:
    initialize_storage()
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO passports (id, created_at, github_username, target_role, payload)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                created_at = excluded.created_at,
                github_username = excluded.github_username,
                target_role = excluded.target_role,
                payload = excluded.payload
            """,
            (
                passport["id"],
                passport["created_at"],
                passport["github_username"],
                passport["target_role"],
                json.dumps(passport, ensure_ascii=False),
            ),
        )


def get_passport(passport_id: str) -> dict[str, Any] | None:
    initialize_storage()
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM passports WHERE id = ?", (passport_id,)
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def list_passports() -> list[dict[str, Any]]:
    initialize_storage()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM passports ORDER BY created_at DESC, id"
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def add_review_request(passport_id: str, citation_key: str, note: str) -> dict[str, Any]:
    initialize_storage()
    request = {
        "id": str(uuid.uuid4()),
        "passport_id": passport_id,
        "citation_key": citation_key,
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
        "reviewed_at": None,
    }
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO review_requests (id, passport_id, citation_key, note, created_at, status, reviewed_at)
            VALUES (:id, :passport_id, :citation_key, :note, :created_at, :status, :reviewed_at)
            """,
            request,
        )
    return request


def list_review_requests(passport_id: str) -> list[dict[str, Any]]:
    initialize_storage()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM review_requests WHERE passport_id = ? ORDER BY created_at, id", (passport_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def mark_review_request_reviewed(request_id: str) -> dict[str, Any] | None:
    initialize_storage()
    with _connect() as connection:
        connection.execute(
            "UPDATE review_requests SET status = 'reviewed', reviewed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), request_id),
        )
        row = connection.execute("SELECT * FROM review_requests WHERE id = ?", (request_id,)).fetchone()
    return dict(row) if row else None


def get_review_requests_by_passports() -> dict[str, list[dict[str, Any]]]:
    """Batch fetch all review requests grouped by passport_id (eliminates N+1 queries)."""
    initialize_storage()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM review_requests ORDER BY created_at, id"
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        grouped.setdefault(item["passport_id"], []).append(item)
    return grouped


def reserve_stored_job(job_id: str, target: str = "", max_active: int = 5) -> tuple[bool, str | None]:
    """Atomically verify active job limits and duplicate targets, then reserve the job slot."""
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    norm_target = target.strip().lower() if target else None
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE;")
        if norm_target:
            existing = connection.execute(
                "SELECT id FROM jobs WHERE target = ? AND status IN ('queued', 'running')",
                (norm_target,),
            ).fetchone()
            if existing:
                return False, "duplicate"

        row = connection.execute(
            "SELECT COUNT(*) as count FROM jobs WHERE status IN ('queued', 'running')"
        ).fetchone()
        if row and int(row["count"]) >= max_active:
            return False, "capacity"

        connection.execute(
            """
            INSERT INTO jobs (id, status, step, completed_steps, error, passport_id, target, created_at, updated_at)
            VALUES (?, 'queued', 'Queued', '[]', NULL, NULL, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = 'queued',
                step = 'Queued',
                completed_steps = '[]',
                error = NULL,
                passport_id = NULL,
                target = excluded.target,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (job_id, norm_target, now, now),
        )
        return True, None


def prune_old_jobs(max_age_days: int = 7) -> int:
    """Prune completed or failed jobs older than max_age_days to keep database minimal."""
    initialize_storage()
    with _connect() as connection:
        cursor = connection.execute(
            """
            DELETE FROM jobs
            WHERE status IN ('done', 'failed')
              AND datetime(created_at) < datetime('now', ?)
            """,
            (f"-{max_age_days} days",),
        )
        return cursor.rowcount


def create_stored_job(job_id: str, target: str = "") -> None:
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    norm_target = target.strip().lower() if target else None
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO jobs (id, status, step, completed_steps, error, passport_id, target, created_at, updated_at)
            VALUES (?, 'queued', 'Queued', '[]', NULL, NULL, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = 'queued',
                step = 'Queued',
                completed_steps = '[]',
                error = NULL,
                passport_id = NULL,
                target = excluded.target,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (job_id, norm_target, now, now),
        )


def count_active_jobs() -> int:
    initialize_storage()
    with _connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) as count FROM jobs WHERE status IN ('queued', 'running')"
        ).fetchone()
    return int(row["count"]) if row else 0


def has_active_job_for_target(target: str) -> bool:
    if not target:
        return False
    initialize_storage()
    with _connect() as connection:
        row = connection.execute(
            "SELECT id FROM jobs WHERE target = ? AND status IN ('queued', 'running')",
            (target.strip().lower(),),
        ).fetchone()
    return bool(row)


def reconcile_interrupted_jobs(stale_seconds: int = 600) -> int:
    """Startup reconciliation: mark uncompleted jobs as interrupted only if they have been
    stale without updates for longer than stale_seconds (default 10 minutes).
    Active jobs owned by other concurrent workers are preserved.
    """
    initialize_storage()
    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                error = 'Job interrupted or timed out. Ready to be retried.'
            WHERE status IN ('queued', 'running')
              AND datetime(COALESCE(updated_at, created_at)) < datetime('now', ? || ' seconds')
            """,
            (f"-{stale_seconds}",),
        )
        return cursor.rowcount


def prune_old_rate_limits(max_age_seconds: int = 3600) -> int:
    """Prune all rate limit records older than max_age_seconds to prevent table growth from inactive keys."""
    initialize_storage()
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_seconds
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM rate_limits WHERE timestamp < ?",
            (cutoff,),
        )
        return cursor.rowcount


def update_stored_job(job_id: str, step: str, completed_steps: list[str]) -> None:
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET status = 'running', step = ?, completed_steps = ?, updated_at = ?
            WHERE id = ?
            """,
            (step, json.dumps(completed_steps), now, job_id),
        )


def complete_stored_job(job_id: str, passport_id: str, completed_steps: list[str]) -> None:
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET status = 'done', step = 'Done', completed_steps = ?, passport_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(completed_steps), passport_id, now, job_id),
        )


def fail_stored_job(job_id: str, error: str) -> None:
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET status = 'failed', error = ?, updated_at = ?
            WHERE id = ?
            """,
            (error, now, job_id),
        )


def get_stored_job(job_id: str) -> dict[str, Any] | None:
    initialize_storage()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["job_id"] = data.pop("id")
    data["completed_steps"] = json.loads(data["completed_steps"])
    return data


def check_storage_rate_limit(key: str, max_requests: int, window_seconds: float) -> bool:
    """Persistent sliding-window rate limit backed by SQLite WAL mode.
    Shared across multiple workers and survives process restarts.
    """
    initialize_storage()
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - window_seconds
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE;")
        connection.execute(
            "DELETE FROM rate_limits WHERE key = ? AND timestamp < ?",
            (key, cutoff),
        )
        row = connection.execute(
            "SELECT COUNT(*) as count FROM rate_limits WHERE key = ?",
            (key,),
        ).fetchone()
        count = int(row["count"]) if row else 0
        if count >= max_requests:
            return False
        connection.execute(
            "INSERT INTO rate_limits (key, timestamp) VALUES (?, ?)",
            (key, now),
        )
        return True

