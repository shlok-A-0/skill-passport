import json
import sqlite3
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
