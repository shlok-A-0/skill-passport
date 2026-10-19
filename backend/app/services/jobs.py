from typing import Any

from app.services.storage import (
    complete_stored_job,
    create_stored_job,
    fail_stored_job,
    get_stored_job,
    update_stored_job,
)

JOB_STEPS = [
    "Reading GitHub",
    "Reading resume",
    "Proposing skills",
    "Verifying evidence",
    "Matching role",
    "Building growth plan",
    "Done",
]


def create_job(job_id: str) -> None:
    create_stored_job(job_id)


def update_job(job_id: str, step: str) -> None:
    completed = [
        item for item in JOB_STEPS[: JOB_STEPS.index(step)]
    ] if step in JOB_STEPS else []
    update_stored_job(job_id, step, completed)


def complete_job(job_id: str, passport_id: str) -> None:
    complete_stored_job(job_id, passport_id, JOB_STEPS.copy())


def fail_job(job_id: str, error: str) -> None:
    fail_stored_job(job_id, error)


def get_job(job_id: str) -> dict[str, Any] | None:
    return get_stored_job(job_id)

