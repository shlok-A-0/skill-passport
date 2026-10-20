import copy
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from app import config
from app.config import (
    ALLOW_DEBUG_ENDPOINTS,
    FRONTEND_ORIGINS,
    RECRUITER_KEY,
    REQUIRE_RECRUITER_AUTH,
    TRUSTED_PROXY_IPS,
)
from app.routes.debug import router as debug_router
from app.services.badge import create_badge
from app.services.framework import get_roles, get_skills
from app.services.github_ingest import GitHubIngestError, validate_github_username
from app.services.identity import OwnershipError, create_challenge, verify_challenge
from app.services.jobs import (
    fail_job,
    get_active_jobs_count,
    get_job,
    is_active_job_running_for,
    reserve_job,
)
from app.services.packets import requirement_packets
from app.services.pipeline import run_passport_job, run_recheck_job
from app.services.storage import (
    check_storage_rate_limit,
    create_review_request,
    get_passport,
    get_passport_owner_token_hash,
    get_review_requests_by_passports,
    list_passports,
    list_review_requests,
    mark_review_request_reviewed,
    prune_old_jobs,
    prune_old_rate_limits,
    reconcile_interrupted_jobs,
)
from app.services.verifier import (
    CHECK_LABELS,
    EXCLUDED_KINDS,
    NOT_WRITTEN_KIND,
    NOT_WRITTEN_REASON,
    citation_key,
)


MAX_CERTIFICATES = 5
MAX_SINGLE_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_REQUEST_BODY_BYTES = MAX_TOTAL_UPLOAD_BYTES + 1024 * 1024


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value for name, value in scope["headers"] if name == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                await self._reject(send, 400, "Invalid Content-Length.")
                return
            if length < 0:
                await self._reject(send, 400, "Invalid Content-Length.")
                return
            if length > self.max_body_size:
                await self._reject(send)
                return

        received = 0
        too_large = False
        error_body_sent = False

        async def limited_receive():
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_size:
                    too_large = True
                    raise RequestBodyTooLarge
            return message

        response_started = False

        async def track_response(message):
            nonlocal response_started, error_body_sent
            if message["type"] == "http.response.start":
                response_started = True
                if too_large:
                    message = {
                        **message,
                        "status": 413,
                        "headers": [(b"content-type", b"application/json")],
                    }
            elif too_large and message["type"] == "http.response.body":
                message = {
                    **message,
                    "body": (
                        b'{"detail":"Request body exceeds the 26 MB limit."}'
                        if not error_body_sent
                        else b""
                    ),
                }
                error_body_sent = True
            await send(message)

        try:
            await self.app(scope, limited_receive, track_response)
        except RequestBodyTooLarge:
            if not response_started:
                await self._reject(send)

    async def _reject(self, send: Send, status: int = 413, detail: str = "Request body exceeds the 26 MB limit.") -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": json.dumps({"detail": detail}).encode(),
            }
        )


class RequestBodyTooLarge(Exception):
    pass


def startup() -> None:
    if REQUIRE_RECRUITER_AUTH and not RECRUITER_KEY:
        raise RuntimeError("RECRUITER_KEY must be configured when recruiter authentication is required.")
    if not config.OWNERSHIP_PROOF_SETTING_VALID:
        raise RuntimeError("REQUIRE_OWNERSHIP_PROOF must be true or false.")
    if not config.REQUIRE_OWNERSHIP_PROOF and config.APP_ENV != "development":
        raise RuntimeError(
            "REQUIRE_OWNERSHIP_PROOF=false is only allowed with APP_ENV=development; "
            "real passports must prove the applicant controls the GitHub account."
        )
    reconcile_interrupted_jobs()
    prune_old_jobs()
    prune_old_rate_limits()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    startup()
    yield


app = FastAPI(title="Skill Passport", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_REQUEST_BODY_BYTES)
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if ALLOW_DEBUG_ENDPOINTS:
    app.include_router(debug_router)

def _rate_limit_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        peer = str(ipaddress.ip_address(peer))
    except ValueError:
        return peer
    if peer not in TRUSTED_PROXY_IPS:
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    chain = [address.strip() for address in forwarded.split(",") if address.strip()]
    current = peer
    for address in reversed(chain):
        try:
            current = str(ipaddress.ip_address(address))
        except ValueError:
            return peer
        if current not in TRUSTED_PROXY_IPS:
            return current
    return current


def check_rate_limit(request: Request, action: str, max_requests: int, window_seconds: float) -> None:
    client_ip = _rate_limit_client_ip(request)
    key = f"{client_ip}:{action}"
    try:
        allowed = check_storage_rate_limit(key, max_requests, window_seconds)
    except Exception as exc:
        logging.getLogger(__name__).error("Rate limiter storage failure for %s: %s", key, exc)
        raise HTTPException(
            status_code=503,
            detail="Rate limiting service temporarily unavailable. Please try again shortly.",
        ) from exc

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for {action}. Please try again later.",
        )


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
    view.pop("resume_skill_mentions", None)
    view.pop("certificate_skill_mentions", None)
    view.pop("identity", None)  # identity checks are recruiter-only
    for skill in view.get("skills", []):
        skill.pop("resume_mention", None)
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


def _valid_passport_token(job: dict[str, Any] | None, token: str | None) -> bool:
    return _valid_token_hash(job.get("access_token_hash") if job else None, token)


def _valid_token_hash(expected: str | None, token: str | None) -> bool:
    if not expected or not token:
        return False
    actual = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(actual, expected)


def _require_passport_access(
    passport_id: str,
    token: str | None,
    recruiter_key: str | None = None,
) -> None:
    if _valid_token_hash(get_passport_owner_token_hash(passport_id), token):
        return
    if RECRUITER_KEY and recruiter_key and hmac.compare_digest(
        recruiter_key.encode(), RECRUITER_KEY.encode()
    ):
        return
    raise HTTPException(status_code=404, detail="Passport not found.")


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


class OwnershipChallengeBody(BaseModel):
    github_username: str = Field(min_length=1, max_length=39)


@app.post("/ownership/challenge")
def ownership_challenge(body: OwnershipChallengeBody, request: Request) -> dict:
    """A one-time code the applicant publishes as a public gist to show they control the account."""
    check_rate_limit(request, "ownership_challenge", max_requests=20, window_seconds=600)
    try:
        username = validate_github_username(body.github_username)
        challenge = create_challenge(username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OwnershipError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        **challenge,
        "instructions": (
            f"Signed in to GitHub as {challenge['github_username']}, create a public gist at "
            f"https://gist.github.com with the description {challenge['code']} (any file name and "
            "content). The code expires in 1 hour and works once; you can delete the gist afterwards."
        ),
    }


async def stream_and_validate_file(
    file: UploadFile,
    max_bytes: int,
    current_aggregate: int,
    max_aggregate: int,
    error_label: str = "File",
    chunk_size: int = 64 * 1024,
) -> tuple[bytes, int]:
    """Stream chunks from upload while enforcing single-file and aggregate limits before full buffering."""
    chunks = []
    file_bytes = 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        file_bytes += len(chunk)
        if file_bytes > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{error_label} must be {max_bytes // (1024 * 1024)} MB or smaller.",
            )
        if current_aggregate + file_bytes > max_aggregate:
            raise HTTPException(
                status_code=413,
                detail=f"Total upload size across resume and certificates must be {max_aggregate // (1024 * 1024)} MB or smaller.",
            )
        chunks.append(chunk)
    if file_bytes == 0:
        raise HTTPException(status_code=422, detail=f"{error_label} must not be empty.")
    return b"".join(chunks), current_aggregate + file_bytes


@app.post("/passport/create", status_code=202)
async def create_passport(
    background_tasks: BackgroundTasks,
    request: Request,
    github_username: str = Form(...),
    resume: UploadFile = File(...),
    target_role: str = Form(...),
    certificates: list[UploadFile] | None = File(default=None),
    is_demo: bool = Form(default=False),
    ownership_challenge_id: str | None = Form(default=None),
) -> dict[str, str]:
    check_rate_limit(request, "create_passport", max_requests=20, window_seconds=600)
    try:
        github_username = validate_github_username(github_username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    access_token = secrets.token_urlsafe(32)
    access_token_hash = hashlib.sha256(access_token.encode()).hexdigest()
    reserved, reason = reserve_job(
        job_id,
        target=candidate_key,
        max_active=5,
        access_token_hash=access_token_hash,
    )
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

    try:
        total_bytes = 0
        resume_bytes, total_bytes = await stream_and_validate_file(
            resume,
            max_bytes=MAX_SINGLE_FILE_BYTES,
            current_aggregate=total_bytes,
            max_aggregate=MAX_TOTAL_UPLOAD_BYTES,
            error_label="Resume PDF",
        )

        cert_bytes = []
        for certificate in certificates or []:
            if certificate.content_type != "application/pdf":
                raise HTTPException(status_code=415, detail="Certificates must be PDF files.")
            content, total_bytes = await stream_and_validate_file(
                certificate,
                max_bytes=MAX_SINGLE_FILE_BYTES,
                current_aggregate=total_bytes,
                max_aggregate=MAX_TOTAL_UPLOAD_BYTES,
                error_label="Each certificate",
            )
            cert_bytes.append(content)
    except Exception as exc:
        fail_job(job_id, f"Upload rejected: {exc}")
        raise

    # Demo passports are labelled DEMO DATA and skip the proof.
    if config.REQUIRE_OWNERSHIP_PROOF and not is_demo:
        try:
            # Calls GitHub, so it runs off the event loop. The proof is bound to this job's passport.
            await run_in_threadpool(verify_challenge, ownership_challenge_id, github_username, job_id)
        except OwnershipError as exc:
            fail_job(job_id, str(exc))
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    background_tasks.add_task(
        run_passport_job,
        job_id,
        github_username,
        target_role,
        resume_bytes,
        cert_bytes,
        is_demo,
    )
    return {"job_id": job_id, "access_token": access_token}


@app.get("/jobs/{job_id}")
def passport_job(
    job_id: str,
    x_passport_token: str | None = Header(default=None),
) -> dict:
    job = get_job(job_id)
    if not job or not _valid_passport_token(job, x_passport_token):
        raise HTTPException(status_code=404, detail="Job not found.")
    return {
        "job_id": job.get("job_id", job_id),
        "status": job.get("status"),
        "step": job.get("step") or "Queued",
        "completed_steps": job.get("completed_steps", []),
        "passport_id": job.get("passport_id"),
        "error": (
            job.get("public_error") or "Passport generation failed. Please try again."
            if job.get("status") == "failed" else None
        ),
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


@app.get("/passports", dependencies=[Depends(require_recruiter)])
def passports() -> list[dict]:
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
def create_passport_review_request(
    passport_id: str,
    body: ReviewRequestBody,
    x_passport_token: str | None = Header(default=None),
) -> dict:
    """A student asks for a human look at an excluded citation. Never changes levels or exclusions."""
    _require_passport_access(passport_id, x_passport_token)
    stored = _stored_passport(passport_id)
    if body.citation_key not in _excluded_keys(stored):
        raise HTTPException(status_code=422, detail="Reviews can only be requested for citations that were not counted.")
    result, reason = create_review_request(
        passport_id, body.citation_key, body.note.strip(), MAX_REVIEW_REQUESTS
    )
    if reason == "limit":
        raise HTTPException(status_code=429, detail=f"At most {MAX_REVIEW_REQUESTS} review requests per passport.")
    if reason == "duplicate":
        raise HTTPException(status_code=409, detail="A review is already requested for this citation.")
    if result is None:
        raise HTTPException(status_code=500, detail="Could not create review request.")
    return result


@app.post("/review-requests/{request_id}/mark-reviewed", dependencies=[Depends(require_recruiter)])
def mark_reviewed(request_id: str) -> dict:
    result = mark_review_request_reviewed(request_id)
    if not result:
        raise HTTPException(status_code=404, detail="Review request not found.")
    return result


@app.post("/passport/{passport_id}/recheck", status_code=202)
def recheck_passport_endpoint(
    passport_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    x_passport_token: str | None = Header(default=None),
    x_recruiter_key: str | None = Header(default=None),
) -> dict:
    _require_passport_access(passport_id, x_passport_token, x_recruiter_key)
    check_rate_limit(request, "recheck_passport", max_requests=10, window_seconds=300)
    existing = get_passport(passport_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Passport not found.")

    target_user = existing.get("github_username") or passport_id
    recheck_job_id = str(uuid.uuid4())
    access_token = secrets.token_urlsafe(32)
    reserved, reason = reserve_job(
        recheck_job_id,
        target=target_user,
        max_active=5,
        access_token_hash=hashlib.sha256(access_token.encode()).hexdigest(),
    )
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

    background_tasks.add_task(run_recheck_job, recheck_job_id, existing)
    return {"job_id": recheck_job_id, "access_token": access_token}


@app.get("/passport/{passport_id}/badge")
def badge(passport_id: str) -> dict:
    result = get_passport(passport_id)
    if not result:
        raise HTTPException(status_code=404, detail="Passport not found.")
    return create_badge(result)
