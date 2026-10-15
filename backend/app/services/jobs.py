from datetime import datetime, timezone
from threading import Lock
from typing import Any

_lock = Lock()
_jobs: dict[str, dict[str, Any]] = {}
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
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "step": "Queued",
            "completed_steps": [],
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


def update_job(job_id: str, step: str) -> None:
    with _lock:
        job = _jobs[job_id]
        job["status"] = "running"
        job["step"] = step
        job["completed_steps"] = [
            item for item in JOB_STEPS[: JOB_STEPS.index(step)]
        ] if step in JOB_STEPS else job["completed_steps"]


def complete_job(job_id: str, passport_id: str) -> None:
    with _lock:
        job = _jobs[job_id]
        job["status"] = "done"
        job["step"] = "Done"
        job["completed_steps"] = JOB_STEPS.copy()
        job["passport_id"] = passport_id


def fail_job(job_id: str, error: str) -> None:
    with _lock:
        job = _jobs[job_id]
        job["status"] = "failed"
        job["error"] = error


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
