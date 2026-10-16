import copy
import hmac
import logging
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi import BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import FRONTEND_ORIGINS, RECRUITER_KEY
from app.routes.debug import router as debug_router
from app.services.badge import create_badge
from app.services.framework import get_roles, get_skills
from app.services.github_ingest import GitHubIngestError
from app.services.jobs import create_job, get_job
from app.services.packets import requirement_packets
from app.services.pipeline import recheck_passport, run_passport_job
from app.services.storage import (
    add_review_request,
    get_passport,
    list_passports,
    list_review_requests,
    mark_review_request_reviewed,
)
from app.services.verifier import (
    CHECK_LABELS,
    EXCLUDED_KINDS,
    NOT_WRITTEN_KIND,
    NOT_WRITTEN_REASON,
    citation_key,
)

app = FastAPI(title="Skill Passport", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(debug_router)


STUDENT_EXCLUDED_REASON = (
    "Matches code published earlier elsewhere, so it isn't counted as your own work. If this is your "
    "work (for example, an earlier project under another account), request a review."
)
MAX_REVIEW_REQUESTS = 10


def _student_excluded_item(item: dict[str, Any], requests: list[dict[str, Any]]) -> dict[str, Any]:
    claim = item.get("claim") or {}
    key = citation_key(claim)
    request = next((r for r in reversed(requests) if r["citation_key"] == key), None)
    not_written = item.get("kind") == NOT_WRITTEN_KIND
    return {
        "kind": NOT_WRITTEN_KIND if not_written else "earlier_source",
        "citation_key": key,
        "claim": {field: claim.get(field) for field in ("skill_id", "repo", "file_path", "start_line", "end_line")},
        "url": item.get("url"),
        "source_url": item.get("source_url"),
        "reason": NOT_WRITTEN_REASON if not_written else STUDENT_EXCLUDED_REASON,
        "review_request": (
            {"status": request["status"], "created_at": request["created_at"]} if request else None
        ),
    }


def student_view(passport: dict[str, Any], requests: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The passport without detector internals (advisory signals and their thresholds would
    teach how to get around the checks), but with every outcome that affects the student:
    each excluded citation is listed so they can request a review."""
    view = copy.deepcopy(passport)
    for skill in view.get("skills", []):
        for item in skill.get("evidence", []):
            item.pop("integrity", None)
            item.pop("record", None)  # the evidence record is for recruiters and the badge
    for entry in view.get("interview_kit", []):
        for question in entry.get("questions", []):
            question.pop("good_answer_mentions", None)
    view["rejected_claims"] = [
        _student_excluded_item(item, requests or []) if item.get("kind") in EXCLUDED_KINDS else item
        for item in view.get("rejected_claims", [])
    ]
    view["check_labels"] = CHECK_LABELS
    return view


def _excluded_keys(passport: dict[str, Any]) -> set[str]:
    return {
        citation_key(item.get("claim") or {})
        for item in passport.get("rejected_claims", [])
        if item.get("kind") in EXCLUDED_KINDS
    }


def review_view(passport: dict[str, Any]) -> dict[str, Any]:
    """The full passport for recruiters, with the student's review requests."""
    return {
        **passport,
        "check_labels": CHECK_LABELS,
        "review_requests": list_review_requests(passport["id"]),
        # Computed on read (deterministic, no LLM), so stored passports get it too.
        "requirement_packets": requirement_packets(passport),
    }


def require_recruiter(x_recruiter_key: str | None = Header(default=None)) -> None:
    """Review endpoints are open in local dev; with RECRUITER_KEY set, the header must match."""
    if RECRUITER_KEY and not hmac.compare_digest(
        (x_recruiter_key or "").encode(), RECRUITER_KEY.encode()
    ):
        raise HTTPException(status_code=401, detail="A valid recruiter key is required.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "skill-passport"}


@app.get("/roles")
def roles() -> list[dict]:
    names = {skill["id"]: skill["name"] for skill in get_skills()}
    return [
        {
            **role,
            "requirements": [
                {**item, "name": names.get(item["skill_id"], item["skill_id"])}
                for item in role["requirements"]
            ],
        }
        for role in get_roles()
    ]


@app.get("/skills")
def skills() -> list[dict[str, str]]:
    return [
        {"id": skill["id"], "name": skill["name"], "category": skill["category"]}
        for skill in get_skills()
    ]


@app.post("/passport/create", status_code=202)
async def create_passport(
    background_tasks: BackgroundTasks,
    github_username: str = Form(...),
    resume: UploadFile = File(...),
    target_role: str = Form(...),
    certificates: list[UploadFile] | None = File(default=None),
    is_demo: bool = Form(default=False),
) -> dict[str, str]:
    if resume.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Resume must be a PDF file.")
    role_ids = {role["id"] for role in get_roles()}
    role_names = {role["name"] for role in get_roles()}
    if target_role not in role_ids | role_names:
        raise HTTPException(status_code=422, detail="Unknown target role.")
    resume_bytes = await resume.read()
    if len(resume_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Resume PDF must be 10 MB or smaller.")
    cert_bytes = []
    for certificate in certificates or []:
        if certificate.content_type != "application/pdf":
            raise HTTPException(status_code=415, detail="Certificates must be PDF files.")
        content = await certificate.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Each certificate must be 10 MB or smaller.")
        cert_bytes.append(content)
    job_id = str(uuid.uuid4())
    create_job(job_id)
    background_tasks.add_task(
        run_passport_job,
        job_id,
        github_username,
        target_role,
        resume_bytes,
        cert_bytes,
        is_demo,
    )
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def passport_job(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def _stored_passport(passport_id: str) -> dict:
    result = get_passport(passport_id)
    if not result:
        raise HTTPException(status_code=404, detail="Passport not found.")
    return result


@app.get("/passport/{passport_id}")
def passport(passport_id: str) -> dict:
    return student_view(_stored_passport(passport_id), list_review_requests(passport_id))


@app.get("/passport/{passport_id}/review", dependencies=[Depends(require_recruiter)])
def review_passport(passport_id: str) -> dict:
    return review_view(_stored_passport(passport_id))


@app.get("/passports")
def passports() -> list[dict]:
    return [student_view(item, list_review_requests(item["id"])) for item in list_passports()]


@app.get("/passports/review", dependencies=[Depends(require_recruiter)])
def review_passports() -> list[dict]:
    return [review_view(item) for item in list_passports()]


class ReviewRequestBody(BaseModel):
    citation_key: str = Field(min_length=1, max_length=600)
    note: str = Field(default="", max_length=1000)


@app.post("/passport/{passport_id}/review-requests", status_code=201)
def create_review_request(passport_id: str, body: ReviewRequestBody) -> dict:
    """A student asks for a human look at an excluded citation. Never changes levels or exclusions."""
    stored = _stored_passport(passport_id)
    if body.citation_key not in _excluded_keys(stored):
        raise HTTPException(status_code=422, detail="Reviews can only be requested for citations that were not counted.")
    existing = list_review_requests(passport_id)
    if len(existing) >= MAX_REVIEW_REQUESTS:
        raise HTTPException(status_code=429, detail=f"At most {MAX_REVIEW_REQUESTS} review requests per passport.")
    if any(item["citation_key"] == body.citation_key and item["status"] == "open" for item in existing):
        raise HTTPException(status_code=409, detail="A review is already requested for this citation.")
    return add_review_request(passport_id, body.citation_key, body.note.strip())


@app.post("/review-requests/{request_id}/mark-reviewed", dependencies=[Depends(require_recruiter)])
def mark_reviewed(request_id: str) -> dict:
    result = mark_review_request_reviewed(request_id)
    if not result:
        raise HTTPException(status_code=404, detail="Review request not found.")
    return result


@app.post("/passport/{passport_id}/recheck")
def recheck_passport_endpoint(passport_id: str) -> dict:
    # Named differently from pipeline.recheck_passport, which this calls.
    existing = get_passport(passport_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Passport not found.")
    try:
        result = recheck_passport(existing)
    except Exception as exc:
        logging.getLogger(__name__).exception("Passport re-check failed: %s", passport_id)
        raise HTTPException(status_code=502, detail=f"Re-check failed: {exc}") from exc
    return {
        "passport": student_view(result, list_review_requests(passport_id)),
        "level_changes": result.get("level_changes", []),
    }


@app.get("/passport/{passport_id}/badge")
def badge(passport_id: str) -> dict:
    result = get_passport(passport_id)
    if not result:
        raise HTTPException(status_code=404, detail="Passport not found.")
    return create_badge(result)
