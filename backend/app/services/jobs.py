from contextlib import contextmanager
from threading import Event, Thread
from typing import Any

from app.services.storage import (
    complete_stored_job,
    count_active_jobs,
    create_stored_job,
    fail_stored_job,
    get_stored_job,
    heartbeat_stored_job,
    has_active_job_for_target,
    reserve_stored_job,
    save_passport_and_complete_job,
    update_stored_job,
)

JOB_STEPS = [
    "Reading GitHub",
    "Reading resume",
    "Reading certificates",
    "Proposing skills",
    "Verifying evidence",
    "Matching role",
    "Building growth plan",
    "Done",
]


def create_job(job_id: str, target: str = "", access_token_hash: str | None = None) -> None:
    create_stored_job(job_id, target, access_token_hash)


def reserve_job(
    job_id: str,
    target: str = "",
    max_active: int = 5,
    access_token_hash: str | None = None,
) -> tuple[bool, str | None]:
    return reserve_stored_job(job_id, target, max_active, access_token_hash)


def update_job(job_id: str, step: str) -> None:
    completed = [
        item for item in JOB_STEPS[: JOB_STEPS.index(step)]
    ] if step in JOB_STEPS else []
    update_stored_job(job_id, step, completed)


def complete_job(job_id: str, passport_id: str) -> None:
    complete_stored_job(job_id, passport_id, JOB_STEPS.copy())


def complete_passport_job(job_id: str, passport: dict[str, Any]) -> None:
    save_passport_and_complete_job(passport, job_id, JOB_STEPS.copy())


def fail_job(job_id: str, error: str, public_error: str | None = None) -> None:
    fail_stored_job(job_id, error, public_error)


def get_job(job_id: str) -> dict[str, Any] | None:
    return get_stored_job(job_id)


def is_active_job_running_for(target: str) -> bool:
    return has_active_job_for_target(target)


def get_active_jobs_count() -> int:
    return count_active_jobs()


@contextmanager
def keep_job_lease(job_id: str):
    stop = Event()

    def heartbeat() -> None:
        while not stop.wait(30):
            heartbeat_stored_job(job_id)

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)
