import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import storage


def test_duplicate_open_reviews_are_migrated_before_unique_index(tmp_path, monkeypatch) -> None:
    database = tmp_path / "legacy.sqlite"
    monkeypatch.setattr(storage, "DATABASE_PATH", database)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, step TEXT NOT NULL,
                completed_steps TEXT NOT NULL DEFAULT '[]', error TEXT, passport_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE review_requests (
                id TEXT PRIMARY KEY, passport_id TEXT NOT NULL, citation_key TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', reviewed_at TEXT
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO review_requests (id, passport_id, citation_key, note, created_at, status)
            VALUES (?, 'p1', 'python:r:a.py:1-2', ?, ?, 'open')
            """,
            [
                ("oldest", "first note", "2026-01-01T00:00:00+00:00"),
                ("duplicate", "second note", "2026-01-02T00:00:00+00:00"),
            ],
        )

    storage.initialize_storage()
    requests = storage.list_review_requests("p1")
    assert [(row["id"], row["note"], row["status"]) for row in requests] == [
        ("oldest", "first note", "open"),
        ("duplicate", "second note", "reviewed"),
    ]
    assert requests[1]["reviewed_at"] == requests[1]["created_at"]
    with storage._connect() as connection:
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(review_requests)")}
    assert "idx_review_requests_one_open" in indexes
    with storage._connect() as connection:
        try:
            connection.execute(
                """
                INSERT INTO review_requests (id, passport_id, citation_key, note, created_at, status)
                VALUES ('third', 'p1', 'python:r:a.py:1-2', '', '2026-01-03', 'open')
                """
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("the partial unique index must reject a second open request")


def test_concurrent_review_creation_enforces_lifetime_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "atomic.sqlite")
    storage.initialize_storage()
    for index in range(9):
        row, error = storage.create_review_request("p1", f"old:{index}", "", 10)
        assert row and error is None
        storage.mark_review_request_reviewed(row["id"], "test-reviewer")

    def create(index: int):
        return storage.create_review_request("p1", f"new:{index}", "", 10)

    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(create, range(5)))
    assert sum(row is not None for row, _ in outcomes) == 1
    assert sum(error == "limit" for _, error in outcomes) == 4
    assert len(storage.list_review_requests("p1")) == 10


def test_review_completion_is_first_writer_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "reviewed.sqlite")
    request, error = storage.create_review_request("p1", "citation", "", 10)
    assert request and error is None

    first = storage.mark_review_request_reviewed(request["id"], "reviewer-a")
    second = storage.mark_review_request_reviewed(request["id"], "reviewer-b")

    assert first and second
    assert second["reviewer_id"] == "reviewer-a"
    assert second["reviewed_at"] == first["reviewed_at"]


def test_readiness_checks_writes_and_rolls_back_probe_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "ready.sqlite")
    storage.check_storage_ready()

    with storage._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM passports").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_passport_and_job_completion_are_atomic_and_keep_owner_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "atomic.sqlite")
    storage.create_stored_job("passport-atomic", access_token_hash="owner-hash")
    passport = {
        "id": "passport-atomic",
        "created_at": "2026-10-21T00:00:00+00:00",
        "github_username": "student",
        "target_role": "role",
    }

    storage.save_passport_and_complete_job(passport, "passport-atomic", ["Done"])

    assert storage.get_passport("passport-atomic") == passport
    assert storage.get_stored_job("passport-atomic")["status"] == "done"
    assert storage.get_passport_owner_token_hash("passport-atomic") == "owner-hash"

    storage.create_stored_job("inactive")
    storage.fail_stored_job("inactive", "failed")
    failed_passport = {**passport, "id": "must-roll-back"}
    try:
        storage.save_passport_and_complete_job(failed_passport, "inactive", ["Done"])
    except RuntimeError:
        pass
    else:
        raise AssertionError("inactive jobs must not commit a passport")
    assert storage.get_passport("must-roll-back") is None


def test_depth_update_merges_fields_only_for_unchanged_passport_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "depth-merge.sqlite")
    original = {
        "id": "depth-merge", "created_at": "created", "updated_at": "version-1",
        "github_username": "student", "target_role": "role", "skills": ["old"],
    }
    storage.save_passport(original)

    latest = {**original, "updated_at": "version-2", "skills": ["new"], "level_changes": ["changed"]}
    storage.save_passport(latest)
    assert not storage.save_passport_depth("depth-merge", "version-1", "created", {"skill_depth": {"x": 1}})
    assert storage.get_passport("depth-merge") == latest

    assert storage.save_passport_depth("depth-merge", "version-2", "created", {"skill_depth": {"x": 1}})
    saved = storage.get_passport("depth-merge")
    assert saved["skills"] == ["new"] and saved["level_changes"] == ["changed"]
    assert saved["skill_depth"] == {"x": 1}


def test_job_reservation_prunes_old_history_and_uses_active_indexes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "job-indexes.sqlite")
    storage.create_stored_job("old")
    storage.complete_stored_job("old", "old-passport", [])
    with storage._connect() as connection:
        connection.execute("UPDATE jobs SET created_at = datetime('now', '-31 days') WHERE id = 'old'")

    reserved, reason = storage.reserve_stored_job("new", target="student")

    assert reserved and reason is None
    assert storage.get_stored_job("old") is None
    with storage._connect() as connection:
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(jobs)")}
    assert {"idx_jobs_active_target", "idx_jobs_active_lease", "idx_jobs_status_created"} <= indexes


def test_latest_student_passports_are_selected_before_paging(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "latest.sqlite")
    for passport_id, username, created, updated in (
        ("new", "same-user", "2026-10-20T00:00:00+00:00", "2026-10-20T00:00:00+00:00"),
        ("old-but-rechecked", "same-user", "2026-10-01T00:00:00+00:00", "2026-10-21T00:00:00+00:00"),
        ("other", "other-user", "2026-10-19T00:00:00+00:00", "2026-10-19T00:00:00+00:00"),
    ):
        storage.save_passport({
            "id": passport_id,
            "created_at": created,
            "updated_at": updated,
            "github_username": username,
            "target_role": "role",
        })

    first_page, total = storage.list_latest_passports_page(1, 0)
    second_page, _ = storage.list_latest_passports_page(1, 1)
    assert total == 2
    assert [item["id"] for item in first_page + second_page] == ["new", "other"]


def test_cleanup_prunes_old_jobs_without_losing_passport_ownership(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "cleanup.sqlite")
    storage.initialize_storage()
    storage.create_stored_job("passport-1", access_token_hash="owner-hash")
    storage.complete_stored_job("passport-1", "passport-1", [])
    storage.create_stored_job("failed")
    storage.fail_stored_job("failed", "failure")
    with storage._connect() as connection:
        connection.execute("UPDATE jobs SET created_at = datetime('now', '-31 days')")
        connection.execute(
            "INSERT INTO rate_limits (key, timestamp) VALUES ('old', strftime('%s','now') - 7200)"
        )
        connection.execute(
            "INSERT INTO rate_limits (key, timestamp) VALUES ('recent', strftime('%s','now'))"
        )

    assert storage.prune_old_jobs() == 2
    assert storage.prune_old_rate_limits() == 1
    assert storage.get_stored_job("passport-1") is None
    assert storage.get_passport_owner_token_hash("passport-1") == "owner-hash"
    with storage._connect() as connection:
        assert connection.execute("SELECT key FROM rate_limits").fetchone()["key"] == "recent"


def test_job_lease_protects_long_running_work_from_startup_reconciliation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "lease.sqlite")
    storage.create_stored_job("active", target="candidate")
    storage.update_stored_job("active", "Reading GitHub", [])
    with storage._connect() as connection:
        connection.execute(
            "UPDATE jobs SET updated_at = datetime('now', '-2 hours'), lease_until = datetime('now', '+2 minutes') WHERE id = 'active'"
        )

    assert storage.reconcile_interrupted_jobs(stale_seconds=600) == 0
    assert storage.get_stored_job("active")["status"] == "running"

    with storage._connect() as connection:
        connection.execute("UPDATE jobs SET lease_until = datetime('now', '-1 second') WHERE id = 'active'")
    assert storage.reconcile_interrupted_jobs(stale_seconds=600) == 1


def test_job_polling_reconciles_an_expired_lease(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "expired-lease.sqlite")
    storage.create_stored_job("expired")
    storage.update_stored_job("expired", "Reading GitHub", [])
    with storage._connect() as connection:
        connection.execute(
            "UPDATE jobs SET lease_until = datetime('now', '-1 second') WHERE id = 'expired'"
        )

    job = storage.get_stored_job("expired")
    assert job and job["status"] == "failed"


def test_expired_job_is_reconciled_during_poll_and_releases_reservation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "expired-lease.sqlite")
    storage.create_stored_job("expired", target="candidate")
    with storage._connect() as connection:
        connection.execute(
            "UPDATE jobs SET status = 'running', lease_until = datetime('now', '-1 second') WHERE id = 'expired'"
        )

    assert storage.get_stored_job("expired")["status"] == "failed"
    assert storage.has_active_job_for_target("candidate") is False
    reserved, reason = storage.reserve_stored_job("replacement", target="candidate")
    assert reserved and reason is None
