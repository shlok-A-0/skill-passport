import copy
import hmac
import logging
import time
import uuid
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    FastAPI,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import (
    ALLOW_DEBUG_ENDPOINTS,
    FRONTEND_ORIGINS,
    RECRUITER_KEY,
    REQUIRE_RECRUITER_AUTH,
)
from app.routes.debug import router as debug_router
from app.services.badge import create_badge
from app.services.framework import get_roles, get_skills
from app.services.github_ingest import GitHubIngestError
from app.services.jobs import (
    complete_job,
    fail_job,
    get_active_jobs_count,
    get_job,
    is_active_job_running_for,
    reserve_job,
    update_job,
)
from app.services.packets import requirement_packets
from app.services.pipeline import recheck_passport, run_passport_job
from app.services.storage import (
    add_review_request,
    get_passport,
    get_review_requests_by_passports,
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
if ALLOW_DEBUG_ENDPOINTS:
    app.include_router(debug_router)

# In-memory sliding window rate limiter per client IP
_ip_rate_records: dict[str, list[float]] = {}


def check_rate_limit(request: Request, action: str, max_requests: int, window_seconds: float) -> None:
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{action}"
    now = time.time()
    records = [t for t in _ip_rate_records.get(key, []) if now - t < window_seconds]
    if len(records) >= max_requests:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for {action}. Please try again later.",
        )
    records.append(now)
    _ip_rate_records[key] = records


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
    """Review endpoints require valid authentication with fail-closed security.

    In production (or when REQUIRE_RECRUITER_AUTH is enabled):
    - If RECRUITER_KEY is not configured on the server, fails closed with 500 (misconfigured security).
    - If X-Recruiter-Key header does not match, fails closed with 401.
    In local development without RECRUITER_KEY, endpoints are open for local dev convenience.
    """
    if REQUIRE_RECRUITER_AUTH and not RECRUITER_KEY:
        raise HTTPException(
            status_code=500,
            detail="Recruiter authentication is required in production, but RECRUITER_KEY is not configured.",
        )
    if RECRUITER_KEY or REQUIRE_RECRUITER_AUTH:
        if not x_recruiter_key or not hmac.compare_digest(
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


MAX_CERTIFICATES = 5
MAX_SINGLE_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_TOTAL_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB aggregate limit


@app.post("/passport/create", status_code=202)
async def create_passport(
    background_tasks: BackgroundTasks,
    request: Request,
    github_username: str = Form(...),
    resume: UploadFile = File(...),
    target_role: str = Form(...),
    certificates: list[UploadFile] | None = File(default=None),
    is_demo: bool = Form(default=False),
) -> dict[str, str]:
    check_rate_limit(request, "create_passport", max_requests=20, window_seconds=600)
    if resume.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Resume must be a PDF file.")
    if certificates and len(certificates) > MAX_CERTIFICATES:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_CERTIFICATES} certificates may be uploaded.",
        )
    role_ids = {role["id"] for role in get_roles()}
    role_names = {role["name"] for role in get_roles()}
    if target_role not in role_ids | role_names:
        raise HTTPException(status_code=422, detail="Unknown target role.")

    # Atomic job slot reservation before accepting large file bodies
    candidate_key = github_username.strip().lower()
    job_id = str(uuid.uuid4())
    reserved, reason = reserve_job(job_id, target=candidate_key, max_active=5)
    if not reserved:
        if reason == "capacity":
            raise HTTPException(
                status_code=429,
                detail="Server is currently processing maximum concurrent verification jobs. Please wait a moment.",
            )
        if reason == "duplicate":
            raise HTTPException(
                status_code=409,
                detail=f"A verification job for '{github_username}' is already in progress.",
            )

    total_bytes = 0
    resume_bytes = await resume.read()
    total_bytes += len(resume_bytes)
    if len(resume_bytes) > MAX_SINGLE_FILE_BYTES:
        fail_job(job_id, "Resume PDF exceeds 10 MB limit.")
        raise HTTPException(status_code=413, detail="Resume PDF must be 10 MB or smaller.")

    cert_bytes = []
    for certificate in certificates or []:
        if certificate.content_type != "application/pdf":
            fail_job(job_id, "Non-PDF certificate rejected.")
            raise HTTPException(status_code=415, detail="Certificates must be PDF files.")
        content = await certificate.read()
        total_bytes += len(content)
        if len(content) > MAX_SINGLE_FILE_BYTES:
            fail_job(job_id, "Certificate PDF exceeds 10 MB limit.")
            raise HTTPException(status_code=413, detail="Each certificate must be 10 MB or smaller.")
        if total_bytes > MAX_TOTAL_UPLOAD_BYTES:
            fail_job(job_id, "Aggregate upload exceeds 25 MB limit.")
            raise HTTPException(
                status_code=413,
                detail="Total upload size across resume and certificates must be 25 MB or smaller.",
            )
        cert_bytes.append(content)

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
    return {
        "job_id": job.get("job_id", job_id),
        "status": job.get("status"),
        "step": job.get("step") or "Queued",
        "completed_steps": job.get("completed_steps", []),
        "passport_id": job.get("passport_id"),
        "error": job.get("error"),
    }


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
def passports(x_recruiter_key: str | None = Header(default=None)) -> list[dict]:
    if REQUIRE_RECRUITER_AUTH:
        require_recruiter(x_recruiter_key)
    all_requests = get_review_requests_by_passports()
    return [student_view(item, all_requests.get(item["id"], [])) for item in list_passports()]


@app.get("/passports/review", dependencies=[Depends(require_recruiter)])
def review_passports() -> list[dict]:
    all_requests = get_review_requests_by_passports()
    return [
        {
            **item,
            "check_labels": CHECK_LABELS,
            "review_requests": all_requests.get(item["id"], []),
            "requirement_packets": requirement_packets(item),
        }
        for item in list_passports()
    ]


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
def recheck_passport_endpoint(passport_id: str, request: Request) -> dict:
    check_rate_limit(request, "recheck_passport", max_requests=10, window_seconds=300)
    existing = get_passport(passport_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Passport not found.")

    target_user = existing.get("github_username") or passport_id
    recheck_job_id = str(uuid.uuid4())
    reserved, reason = reserve_job(recheck_job_id, target=target_user, max_active=5)
    if not reserved:
        if reason == "duplicate":
            raise HTTPException(
                status_code=409,
                detail=f"A verification or re-check job for '{target_user}' is already in progress.",
            )
        if reason == "capacity":
            raise HTTPException(
                status_code=429,
                detail="Server is currently processing maximum concurrent verification jobs. Please wait a moment.",
            )

    try:
        update_job(recheck_job_id, "Reading GitHub")
        result = recheck_passport(existing)
        complete_job(recheck_job_id, passport_id)
    except Exception as exc:
        fail_job(recheck_job_id, str(exc))
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
