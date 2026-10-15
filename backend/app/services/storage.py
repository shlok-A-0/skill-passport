import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import DATABASE_PATH


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_storage() -> None:
    with _connect() as connection:
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
