import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import DATABASE_PATH


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


_initialized_dbs: set[str] = set()


def _reconcile_expired_jobs(connection: sqlite3.Connection, stale_seconds: int = 120) -> int:
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = 'failed',
            error = 'Job interrupted or timed out. Ready to be retried.',
            public_error = 'The verification took too long and was stopped. Please try again.',
            lease_until = NULL
        WHERE status IN ('queued', 'running')
          AND datetime(COALESCE(
              lease_until,
              datetime(COALESCE(updated_at, created_at), '+' || ? || ' seconds')
          )) < datetime('now')
        """,
        (stale_seconds,),
    )
    return cursor.rowcount


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
                public_error TEXT,
                passport_id TEXT,
                access_token_hash TEXT,
                target TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                lease_until TEXT
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
        try:
            connection.execute("ALTER TABLE jobs ADD COLUMN access_token_hash TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            connection.execute("ALTER TABLE jobs ADD COLUMN lease_until TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            connection.execute("ALTER TABLE jobs ADD COLUMN public_error TEXT")
        except sqlite3.OperationalError:
            pass
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS passport_owners (
                passport_id TEXT PRIMARY KEY,
                access_token_hash TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO passport_owners (passport_id, access_token_hash)
            SELECT COALESCE(passport_id, id), access_token_hash
            FROM jobs
            WHERE status = 'done' AND access_token_hash IS NOT NULL
            """
        )
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
                reviewed_at TEXT,
                reviewer_id TEXT
            )
            """
        )
        try:
            connection.execute("ALTER TABLE review_requests ADD COLUMN reviewer_id TEXT")
        except sqlite3.OperationalError:
            pass
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
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_limits_timestamp ON rate_limits (timestamp)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_active_target ON jobs (target) "
            "WHERE status IN ('queued', 'running')"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_active_lease ON jobs (lease_until) "
            "WHERE status IN ('queued', 'running')"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs (status, created_at)"
        )
        # Retain duplicate request rows as reviewed history while keeping one open row.
        duplicates = connection.execute(
            """
            SELECT passport_id, citation_key
            FROM review_requests
            WHERE status = 'open'
            GROUP BY passport_id, citation_key
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for duplicate in duplicates:
            rows = connection.execute(
                """
                SELECT id, created_at FROM review_requests
                WHERE passport_id = ? AND citation_key = ? AND status = 'open'
                ORDER BY created_at, id
                """,
                (duplicate["passport_id"], duplicate["citation_key"]),
            ).fetchall()
            for row in rows[1:]:
                connection.execute(
                    """
                    UPDATE review_requests
                    SET status = 'reviewed', reviewed_at = COALESCE(reviewed_at, created_at)
                    WHERE id = ?
                    """,
                    (row["id"],),
                )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_review_requests_one_open
            ON review_requests (passport_id, citation_key) WHERE status = 'open'
            """
        )
        # Target roles built from a pasted job description. The description itself is not stored.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_roles (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                requirements TEXT NOT NULL,
                dropped TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """
        )
        # Recruiter sign-in: a recruiter key is exchanged once for a short-lived session token;
        # only the token's hash is stored.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS recruiter_sessions (
                token_hash TEXT PRIMARY KEY,
                recruiter_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        # Private recruiter interview sessions. Kept out of the passport payload, so no student
        # endpoint can return them. Outcomes are human observations and never change evidence.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS interview_sessions (
                id TEXT PRIMARY KEY,
                passport_id TEXT NOT NULL,
                recruiter_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source_revision TEXT,
                error TEXT,
                payload TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_interview_sessions_idempotency
            ON interview_sessions (recruiter_id, passport_id, idempotency_key)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS interview_questions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                skill_id TEXT NOT NULL,
                question_type TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_interview_questions_session ON interview_questions (session_id, position)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS interview_assessments (
                question_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                assessor_id TEXT NOT NULL,
                assessed_at TEXT NOT NULL
            )
            """
        )
    _initialized_dbs.add(db_key)


def check_storage_ready() -> None:
    initialize_storage()
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO passports (id, created_at, github_username, target_role, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(), "readiness", "readiness", "{}"),
        )
        connection.execute(
            "INSERT INTO jobs (id, status, step, created_at) VALUES (?, 'queued', 'Queued', ?)",
            (str(uuid.uuid4()), datetime.now(timezone.utc).isoformat()),
        )
        connection.rollback()


def save_passport(passport: dict[str, Any]) -> None:
    initialize_storage()
    with _connect() as connection:
        _save_passport(connection, passport)


def save_passport_depth(
    passport_id: str,
    expected_updated_at: str | None,
    expected_created_at: str,
    fields: dict[str, Any],
) -> bool:
    """Merge missing depth fields only if the stored passport is still the analyzed version."""
    initialize_storage()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE;")
        row = connection.execute("SELECT payload FROM passports WHERE id = ?", (passport_id,)).fetchone()
        if not row:
            return False
        passport = json.loads(row["payload"])
        if (
            passport.get("updated_at") != expected_updated_at
            or passport.get("created_at") != expected_created_at
        ):
            return False
        missing = {
            key: value for key, value in fields.items()
            if value is not None and passport.get(key) is None
        }
        if not missing:
            return False
        passport.update(missing)
        connection.execute(
            "UPDATE passports SET payload = ? WHERE id = ?",
            (json.dumps(passport, ensure_ascii=False), passport_id),
        )
        return True


def _save_passport(connection: sqlite3.Connection, passport: dict[str, Any]) -> None:
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


def save_passport_and_complete_job(
    passport: dict[str, Any], job_id: str, completed_steps: list[str]
) -> None:
    """Persist the result, job status, and initial owner token in one transaction."""
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE;")
        _save_passport(connection, passport)
        cursor = connection.execute(
            """
            UPDATE jobs
            SET status = 'done', step = 'Done', completed_steps = ?, passport_id = ?,
                updated_at = ?, lease_until = NULL
            WHERE id = ? AND status IN ('queued', 'running')
            """,
            (json.dumps(completed_steps), passport["id"], now, job_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Cannot complete a missing or inactive passport job.")
        if job_id == passport["id"]:
            row = connection.execute(
                "SELECT access_token_hash FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row and row["access_token_hash"]:
                connection.execute(
                    """
                    INSERT INTO passport_owners (passport_id, access_token_hash) VALUES (?, ?)
                    ON CONFLICT(passport_id) DO UPDATE SET access_token_hash = excluded.access_token_hash
                    """,
                    (passport["id"], row["access_token_hash"]),
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


def list_passports_page(limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Return a bounded, newest-first page and the total count."""
    initialize_storage()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM passports ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        total = connection.execute("SELECT COUNT(*) AS count FROM passports").fetchone()["count"]
    return [json.loads(row["payload"]) for row in rows], total


def list_latest_passports_page(limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Return the newest-created passport per student before applying pagination."""
    initialize_storage()
    with _connect() as connection:
        rows = connection.execute(
            """
            WITH ranked AS (
                SELECT id, created_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY lower(github_username)
                           ORDER BY created_at DESC, id
                       ) AS student_rank
                FROM passports
            )
            SELECT passports.payload
            FROM passports
            JOIN ranked ON ranked.id = passports.id
            WHERE ranked.student_rank = 1
            ORDER BY ranked.created_at DESC, ranked.id
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        total = connection.execute(
            "SELECT COUNT(DISTINCT lower(github_username)) AS count FROM passports"
        ).fetchone()["count"]
    return [json.loads(row["payload"]) for row in rows], total


def save_custom_role(role: dict[str, Any]) -> None:
    initialize_storage()
    with _connect() as connection:
        connection.execute(
            "INSERT INTO custom_roles (id, title, requirements, dropped, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                role["id"],
                role["title"],
                json.dumps(role["requirements"], ensure_ascii=False),
                json.dumps(role.get("dropped", []), ensure_ascii=False),
                role["created_at"],
            ),
        )


def get_custom_role(role_id: str) -> dict[str, Any] | None:
    initialize_storage()
    with _connect() as connection:
        row = connection.execute("SELECT * FROM custom_roles WHERE id = ?", (role_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "requirements": json.loads(row["requirements"]),
        "dropped": json.loads(row["dropped"]),
        "created_at": row["created_at"],
    }


def create_review_request(
    passport_id: str, citation_key: str, note: str, max_requests: int
) -> tuple[dict[str, Any] | None, str | None]:
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
        connection.execute("BEGIN IMMEDIATE;")
        duplicate = connection.execute(
            """
            SELECT 1 FROM review_requests
            WHERE passport_id = ? AND citation_key = ? AND status = 'open'
            """,
            (passport_id, citation_key),
        ).fetchone()
        if duplicate:
            return None, "duplicate"
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM review_requests WHERE passport_id = ?",
            (passport_id,),
        ).fetchone()["count"]
        if count >= max_requests:
            return None, "limit"
        connection.execute(
            """
            INSERT INTO review_requests (id, passport_id, citation_key, note, created_at, status, reviewed_at)
            VALUES (:id, :passport_id, :citation_key, :note, :created_at, :status, :reviewed_at)
            """,
            request,
        )
    return request, None


def list_review_requests(passport_id: str) -> list[dict[str, Any]]:
    initialize_storage()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM review_requests WHERE passport_id = ? ORDER BY created_at, id", (passport_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def mark_review_request_reviewed(request_id: str, reviewer_id: str) -> dict[str, Any] | None:
    initialize_storage()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE review_requests
            SET status = 'reviewed', reviewed_at = ?, reviewer_id = ?
            WHERE id = ? AND status = 'open'
            """,
            (datetime.now(timezone.utc).isoformat(), reviewer_id, request_id),
        )
        row = connection.execute("SELECT * FROM review_requests WHERE id = ?", (request_id,)).fetchone()
    return dict(row) if row else None


def get_review_requests_by_passports(passport_ids: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Batch fetch requests for a passport page (or all when called without IDs)."""
    if passport_ids is not None and not passport_ids:
        return {}
    initialize_storage()
    with _connect() as connection:
        if passport_ids is None:
            rows = connection.execute("SELECT * FROM review_requests ORDER BY created_at, id").fetchall()
        else:
            placeholders = ",".join("?" for _ in passport_ids)
            rows = connection.execute(
                f"SELECT * FROM review_requests WHERE passport_id IN ({placeholders}) ORDER BY created_at, id",
                passport_ids,
            ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        grouped.setdefault(item["passport_id"], []).append(item)
    return grouped


def reserve_stored_job(
    job_id: str,
    target: str = "",
    max_active: int = 5,
    access_token_hash: str | None = None,
) -> tuple[bool, str | None]:
    """Atomically verify active job limits and duplicate targets, then reserve the job slot."""
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    lease_until = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
    norm_target = target.strip().lower() if target else None
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE;")
        connection.execute(
            "DELETE FROM jobs WHERE status IN ('done', 'failed') "
            "AND datetime(created_at) < datetime('now', '-30 days')"
        )
        _reconcile_expired_jobs(connection)
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
            INSERT INTO jobs (id, status, step, completed_steps, error, public_error, passport_id, access_token_hash, target, created_at, updated_at, lease_until)
            VALUES (?, 'queued', 'Queued', '[]', NULL, NULL, NULL, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = 'queued',
                step = 'Queued',
                completed_steps = '[]',
                error = NULL,
                public_error = NULL,
                passport_id = NULL,
                access_token_hash = excluded.access_token_hash,
                target = excluded.target,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                lease_until = excluded.lease_until
            """,
            (job_id, access_token_hash, norm_target, now, now, lease_until),
        )
        return True, None


def prune_old_jobs(max_age_days: int = 30) -> int:
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


def create_stored_job(
    job_id: str,
    target: str = "",
    access_token_hash: str | None = None,
) -> None:
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    lease_until = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
    norm_target = target.strip().lower() if target else None
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO jobs (id, status, step, completed_steps, error, public_error, passport_id, access_token_hash, target, created_at, updated_at, lease_until)
            VALUES (?, 'queued', 'Queued', '[]', NULL, NULL, NULL, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = 'queued',
                step = 'Queued',
                completed_steps = '[]',
                error = NULL,
                public_error = NULL,
                passport_id = NULL,
                access_token_hash = excluded.access_token_hash,
                target = excluded.target,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                lease_until = excluded.lease_until
            """,
            (job_id, access_token_hash, norm_target, now, now, lease_until),
        )


def count_active_jobs() -> int:
    initialize_storage()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE;")
        _reconcile_expired_jobs(connection)
        row = connection.execute(
            "SELECT COUNT(*) as count FROM jobs WHERE status IN ('queued', 'running')"
        ).fetchone()
    return int(row["count"]) if row else 0


def has_active_job_for_target(target: str) -> bool:
    if not target:
        return False
    initialize_storage()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE;")
        _reconcile_expired_jobs(connection)
        row = connection.execute(
            "SELECT id FROM jobs WHERE target = ? AND status IN ('queued', 'running')",
            (target.strip().lower(),),
        ).fetchone()
    return bool(row)


def reconcile_interrupted_jobs(stale_seconds: int = 120) -> int:
    """Fail active jobs only after their worker heartbeat lease has expired."""
    initialize_storage()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE;")
        return _reconcile_expired_jobs(connection, stale_seconds)


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
    lease_until = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET status = 'running', step = ?, completed_steps = ?, updated_at = ?, lease_until = ?
            WHERE id = ?
            """,
            (step, json.dumps(completed_steps), now, lease_until, job_id),
        )


def heartbeat_stored_job(job_id: str) -> None:
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    lease_until = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE jobs SET updated_at = ?, lease_until = ?
            WHERE id = ? AND status IN ('queued', 'running')
            """,
            (now, lease_until, job_id),
        )


def complete_stored_job(job_id: str, passport_id: str, completed_steps: list[str]) -> None:
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET status = 'done', step = 'Done', completed_steps = ?, passport_id = ?, updated_at = ?, lease_until = NULL
            WHERE id = ?
            """,
            (json.dumps(completed_steps), passport_id, now, job_id),
        )
        row = connection.execute(
            "SELECT access_token_hash FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if job_id == passport_id and row and row["access_token_hash"]:
            connection.execute(
                """
                INSERT INTO passport_owners (passport_id, access_token_hash) VALUES (?, ?)
                ON CONFLICT(passport_id) DO UPDATE SET access_token_hash = excluded.access_token_hash
                """,
                (passport_id, row["access_token_hash"]),
            )


def fail_stored_job(job_id: str, error: str, public_error: str | None = None) -> None:
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET status = 'failed', error = ?, public_error = ?, updated_at = ?, lease_until = NULL
            WHERE id = ?
            """,
            (error, public_error, now, job_id),
        )


def get_stored_job(job_id: str) -> dict[str, Any] | None:
    initialize_storage()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row and row["status"] in {"queued", "running"}:
            lease_expired = connection.execute(
                """
                SELECT datetime(COALESCE(
                    lease_until,
                    datetime(COALESCE(updated_at, created_at), '+120 seconds')
                )) < datetime('now')
                FROM jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()[0]
            if lease_expired:
                connection.execute("BEGIN IMMEDIATE;")
                _reconcile_expired_jobs(connection)
                row = connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["job_id"] = data.pop("id")
    data["completed_steps"] = json.loads(data["completed_steps"])
    return data


def get_passport_owner_token_hash(passport_id: str) -> str | None:
    initialize_storage()
    with _connect() as connection:
        row = connection.execute(
            "SELECT access_token_hash FROM passport_owners WHERE passport_id = ?",
            (passport_id,),
        ).fetchone()
    return row["access_token_hash"] if row else None


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
            "DELETE FROM rate_limits WHERE timestamp < ?",
            (now - 3600,),
        )
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


def create_recruiter_session(recruiter_id: str, token_hash: str, ttl_seconds: int) -> str:
    """Store a sign-in session (token hash only) and prune expired ones; returns expires_at."""
    initialize_storage()
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
    with _connect() as connection:
        connection.execute("DELETE FROM recruiter_sessions WHERE expires_at < ?", (now.isoformat(),))
        connection.execute(
            "INSERT INTO recruiter_sessions (token_hash, recruiter_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, recruiter_id, now.isoformat(), expires_at),
        )
    return expires_at


def get_recruiter_session(token_hash: str) -> str | None:
    """The recruiter id for an unexpired session token hash."""
    initialize_storage()
    with _connect() as connection:
        row = connection.execute(
            "SELECT recruiter_id FROM recruiter_sessions WHERE token_hash = ? AND expires_at > ?",
            (token_hash, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    return row["recruiter_id"] if row else None


def delete_recruiter_session(token_hash: str) -> None:
    initialize_storage()
    with _connect() as connection:
        connection.execute("DELETE FROM recruiter_sessions WHERE token_hash = ?", (token_hash,))


# A "generating" session with no heartbeat for this long was interrupted (for example by a
# restart): the route renews the reservation with touch_interview_session while work runs.
INTERVIEW_GENERATION_TIMEOUT_SECONDS = 300
INTERRUPTED_INTERVIEW_ERROR = "Generation was interrupted. Start a new interview set."


def _interview_session_row(row: sqlite3.Row) -> dict[str, Any]:
    session = dict(row)
    session["payload"] = json.loads(session["payload"] or "{}")
    if session["status"] == "generating":
        heartbeat = datetime.fromisoformat(session["updated_at"] or session["created_at"])
        if datetime.now(timezone.utc) - heartbeat > timedelta(seconds=INTERVIEW_GENERATION_TIMEOUT_SECONDS):
            session["status"] = "failed"
            session["error"] = INTERRUPTED_INTERVIEW_ERROR
    return session


def start_interview_session(
    passport_id: str, recruiter_id: str, idempotency_key: str, source_revision: str | None
) -> tuple[dict[str, Any], str]:
    """Reserve a session row before calling the model.

    Returns (session, outcome): "created" for a new reservation, "existing" when this recruiter
    already used the idempotency key for this passport (a retry or double submit), or "busy"
    when another set for this passport is still being generated by the same recruiter.
    """
    initialize_storage()
    now = datetime.now(timezone.utc)
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE;")
        existing = connection.execute(
            """
            SELECT * FROM interview_sessions
            WHERE recruiter_id = ? AND passport_id = ? AND idempotency_key = ?
            """,
            (recruiter_id, passport_id, idempotency_key),
        ).fetchone()
        if existing:
            return _interview_session_row(existing), "existing"
        cutoff = (now - timedelta(seconds=INTERVIEW_GENERATION_TIMEOUT_SECONDS)).isoformat()
        busy = connection.execute(
            """
            SELECT * FROM interview_sessions
            WHERE recruiter_id = ? AND passport_id = ? AND status = 'generating' AND updated_at > ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (recruiter_id, passport_id, cutoff),
        ).fetchone()
        if busy:
            return _interview_session_row(busy), "busy"
        # No live reservation: anything still marked "generating" past the timeout is interrupted
        # work. End it before allowing a replacement, so the original request cannot finish later
        # and save over the new set.
        connection.execute(
            """
            UPDATE interview_sessions SET status = 'failed', error = ?, updated_at = ?
            WHERE recruiter_id = ? AND passport_id = ? AND status = 'generating'
            """,
            (INTERRUPTED_INTERVIEW_ERROR, now.isoformat(), recruiter_id, passport_id),
        )
        session_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO interview_sessions
                (id, passport_id, recruiter_id, idempotency_key, status, created_at, updated_at, source_revision)
            VALUES (?, ?, ?, ?, 'generating', ?, ?, ?)
            """,
            (session_id, passport_id, recruiter_id, idempotency_key, now.isoformat(), now.isoformat(), source_revision),
        )
        row = connection.execute("SELECT * FROM interview_sessions WHERE id = ?", (session_id,)).fetchone()
    return _interview_session_row(row), "created"


def touch_interview_session(session_id: str) -> None:
    """Renew a running generation's reservation. The generation route calls this periodically so
    slow-but-alive work is never reported as interrupted or replaced by a second generation."""
    initialize_storage()
    with _connect() as connection:
        connection.execute(
            "UPDATE interview_sessions SET updated_at = ? WHERE id = ? AND status = 'generating'",
            (datetime.now(timezone.utc).isoformat(), session_id),
        )


def complete_interview_session(
    session_id: str, payload: dict[str, Any], questions: list[dict[str, Any]]
) -> None:
    """Save validated questions and mark the session ready, in one transaction."""
    initialize_storage()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE;")
        for position, question in enumerate(questions):
            connection.execute(
                """
                INSERT INTO interview_questions (id, session_id, position, skill_id, question_type, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    question["id"], session_id, position, question["skill_id"],
                    question["question_type"], json.dumps(question, ensure_ascii=False),
                ),
            )
        cursor = connection.execute(
            """
            UPDATE interview_sessions SET status = 'ready', payload = ?, updated_at = ?, error = NULL
            WHERE id = ? AND status = 'generating'
            """,
            (json.dumps(payload, ensure_ascii=False), now, session_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise RuntimeError("Cannot complete a missing or finished interview session.")


def fail_interview_session(session_id: str, error: str) -> None:
    initialize_storage()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE interview_sessions SET status = 'failed', error = ?, updated_at = ?
            WHERE id = ? AND status = 'generating'
            """,
            (error, datetime.now(timezone.utc).isoformat(), session_id),
        )


def get_interview_session(session_id: str) -> dict[str, Any] | None:
    """The session with its questions (in order) and saved assessments keyed by question id."""
    initialize_storage()
    with _connect() as connection:
        row = connection.execute("SELECT * FROM interview_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        questions = connection.execute(
            "SELECT payload FROM interview_questions WHERE session_id = ? ORDER BY position", (session_id,)
        ).fetchall()
        assessments = connection.execute(
            "SELECT * FROM interview_assessments WHERE session_id = ?", (session_id,)
        ).fetchall()
    session = _interview_session_row(row)
    session["questions"] = [json.loads(item["payload"]) for item in questions]
    session["assessments"] = {item["question_id"]: dict(item) for item in assessments}
    return session


def list_interview_sessions(passport_id: str, recruiter_id: str) -> list[dict[str, Any]]:
    """This recruiter's sessions for one passport, newest first, with questions and assessments."""
    initialize_storage()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id FROM interview_sessions WHERE passport_id = ? AND recruiter_id = ?
            ORDER BY created_at DESC, id
            """,
            (passport_id, recruiter_id),
        ).fetchall()
    return [session for row in rows if (session := get_interview_session(row["id"]))]


def save_interview_assessment(
    session_id: str, question_id: str, outcome: str, note: str, assessor_id: str
) -> dict[str, Any] | None:
    """Create or replace the outcome for one question; None if the question isn't in the session."""
    initialize_storage()
    assessment = {
        "question_id": question_id,
        "session_id": session_id,
        "outcome": outcome,
        "note": note,
        "assessor_id": assessor_id,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE;")
        belongs = connection.execute(
            "SELECT 1 FROM interview_questions WHERE id = ? AND session_id = ?", (question_id, session_id)
        ).fetchone()
        if not belongs:
            return None
        connection.execute(
            """
            INSERT INTO interview_assessments (question_id, session_id, outcome, note, assessor_id, assessed_at)
            VALUES (:question_id, :session_id, :outcome, :note, :assessor_id, :assessed_at)
            ON CONFLICT(question_id) DO UPDATE SET
                outcome = excluded.outcome, note = excluded.note,
                assessor_id = excluded.assessor_id, assessed_at = excluded.assessed_at
            """,
            assessment,
        )
    return assessment
