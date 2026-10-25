import copy
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Lock
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Query,
    Response,
    UploadFile,
    FastAPI,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import config
from app.config import (
    ALLOW_DEBUG_ENDPOINTS,
    FRONTEND_ORIGINS,
    RECRUITER_ACCOUNTS,
    RECRUITER_KEY,
    REQUIRE_RECRUITER_AUTH,
    TRUSTED_PROXY_IPS,
)
from app.routes.debug import router as debug_router
from app.services.badge import create_badge
from app.services.framework import get_roles, get_skills
from app.services.github_ingest import validate_github_username
from app.services.identity import OwnershipError, create_challenge, verify_challenge
from app.services.interview_sessions import (
    OUTCOMES,
    InterviewGenerationError,
    build_interview_set,
    session_view,
    source_revision,
)
from app.services.jd_parse import (
    CUSTOM_ROLE_PREFIX,
    JobDescriptionError,
    build_custom_role,
    role_definition,
)
from app.services.jobs import (
    fail_job,
    get_job,
    reserve_job,
)
from app.services.json_cache import prune_json_cache
from app.services.packets import requirement_packets
from app.services.codeforces_ingest import validate_codeforces_handle
from app.services.pipeline import ensure_depth_analyzers, run_passport_job, run_recheck_job
from app.services.report_parse import MAX_REPORTS, safe_filename, validate_portfolio_url
from app.services.sql_depth import student_sql_depth
from app.services.skill_depth import student_skill_depth
from app.services.storage import (
    check_storage_rate_limit,
    check_storage_ready,
    complete_interview_session,
    create_recruiter_session,
    create_review_request,
    delete_recruiter_session,
    fail_interview_session,
    get_custom_role,
    get_interview_session,
    get_passport,
    get_passport_owner_token_hash,
    get_recruiter_session,
    get_review_requests_by_passports,
    list_interview_sessions,
    list_passports_page,
    list_latest_passports_page,
    list_review_requests,
    mark_review_request_reviewed,
    prune_old_jobs,
    prune_old_rate_limits,
    reconcile_interrupted_jobs,
    save_custom_role,
    save_interview_assessment,
    start_interview_session,
    touch_interview_session,
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


def _supported_certificate_bytes(content: bytes) -> bool:
    return (
        content.startswith(b"%PDF-")
        or content.startswith(b"\x89PNG\r\n\x1a\n")
        or content.startswith(b"\xff\xd8\xff")
    )


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


class PassportCreateRateLimitMiddleware:
    """Reject excess passport uploads before FastAPI parses multipart bodies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/passport/create":
            await self.app(scope, receive, send)
            return

        try:
            check_rate_limit(Request(scope), "create_passport", max_requests=20, window_seconds=600)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def startup() -> None:
    if REQUIRE_RECRUITER_AUTH and (
        not config.RECRUITER_ACCOUNTS_SETTING_VALID or not RECRUITER_ACCOUNTS
    ):
        raise RuntimeError(
            "RECRUITER_ACCOUNTS must be a JSON object of named keys (each secret at least 32 characters)."
        )
    if not config.OWNERSHIP_PROOF_SETTING_VALID:
        raise RuntimeError("REQUIRE_OWNERSHIP_PROOF must be true or false.")
    if not config.REQUIRE_OWNERSHIP_PROOF and config.APP_ENV != "development":
        raise RuntimeError(
            "REQUIRE_OWNERSHIP_PROOF=false is only allowed with APP_ENV=development; "
            "real passports must prove the applicant controls the GitHub account."
        )
    if config.APP_ENV != "development":
        missing = [
            name for name, value in (
                ("GITHUB_TOKEN", config.GITHUB_TOKEN),
                ("GEMINI_API_KEY", config.GEMINI_API_KEY),
            ) if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required production setting(s): {', '.join(missing)}.")
    reconcile_interrupted_jobs()
    prune_old_jobs()
    prune_old_rate_limits()
    prune_json_cache(config.CACHE_DIR)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    startup()
    yield


app = FastAPI(title="Skill Passport", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_REQUEST_BODY_BYTES)
app.add_middleware(PassportCreateRateLimitMiddleware)
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
    for report in view.get("reports", []):
        # A link to a repository on another account is a recruiter-only signal.
        report["repo_links"] = [link for link in report.get("repo_links", []) if link.get("status") == "own"]
    for skill in view.get("skills", []):
        skill.pop("resume_mention", None)
        skill.pop("report_mention", None)
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
    if "sql_depth" in view:
        if config.ENABLE_SQL_DEPTH:
            view["sql_depth"] = student_sql_depth(view["sql_depth"])
        else:
            view.pop("sql_depth")
    if "skill_depth" in view:
        if config.ENABLE_SKILL_DEPTH:
            view["skill_depth"] = student_skill_depth(view["skill_depth"])
            if not config.ENABLE_SQL_DEPTH and view["skill_depth"]:
                view["skill_depth"].pop("sql", None)
        else:
            view.pop("skill_depth")
    return view


def _without_disabled_features(passport: dict[str, Any]) -> dict[str, Any]:
    """Stored results of a feature that is now switched off are not returned."""
    view = {key: value for key, value in passport.items()}
    if not config.ENABLE_SQL_DEPTH:
        view.pop("sql_depth", None)
    if not config.ENABLE_SKILL_DEPTH:
        view.pop("skill_depth", None)
    elif not config.ENABLE_SQL_DEPTH and isinstance(view.get("skill_depth"), dict):
        view["skill_depth"] = copy.deepcopy(view["skill_depth"])
        view["skill_depth"].pop("sql", None)
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
        **_without_disabled_features(passport),
        "check_labels": CHECK_LABELS,
        "review_requests": list_review_requests(passport["id"]),
        # Computed on read (deterministic, no LLM), so stored passports get it too.
        "requirement_packets": requirement_packets(passport),
    }


def _recruiter_id_for_key(key: str | None) -> str | None:
    if not key:
        return None
    matched_ids = [
        recruiter_id
        for recruiter_id, expected in RECRUITER_ACCOUNTS.items()
        if hmac.compare_digest(key.encode(), expected.encode())
    ]
    if matched_ids:
        return matched_ids[0]
    if not REQUIRE_RECRUITER_AUTH and RECRUITER_KEY and hmac.compare_digest(
        key.encode(), RECRUITER_KEY.encode()
    ):
        return "local-recruiter"
    return None


# A signed-in recruiter session lasts one working day; signing in again issues a new token.
RECRUITER_SESSION_SECONDS = 8 * 3600

# How often a running interview generation renews its reservation (see storage.py: the
# interruption timeout counts from the last renewal, well above this cadence).
INTERVIEW_SESSION_HEARTBEAT_SECONDS = 60


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def _recruiter_id_for_session(authorization: str | None) -> str | None:
    """The recruiter behind a sign-in token, only while their key is still configured, so
    removing a key from RECRUITER_ACCOUNTS also ends that recruiter's sessions."""
    token = _bearer_token(authorization)
    if not token:
        return None
    recruiter_id = get_recruiter_session(hashlib.sha256(token.encode()).hexdigest())
    if recruiter_id in RECRUITER_ACCOUNTS:
        return recruiter_id
    if recruiter_id == "local-recruiter" and not REQUIRE_RECRUITER_AUTH and RECRUITER_KEY:
        return recruiter_id
    return None


def _recruiter_id_from_credentials(key: str | None, authorization: str | None) -> str | None:
    return _recruiter_id_for_key(key) or _recruiter_id_for_session(authorization)


def require_recruiter(
    x_recruiter_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> str:
    """Review endpoints require valid authentication with fail-closed security.

    Credentials: a sign-in token (`Authorization: Bearer`, from POST /auth/recruiter/login) or
    the recruiter key itself (`X-Recruiter-Key`, for scripts). The role always comes from these
    server-checked credentials, never from a request parameter.

    In production (or when REQUIRE_RECRUITER_AUTH is enabled):
    - If no named recruiter keys are configured, fails closed with 500 (misconfigured security).
    - If no credential matches, fails closed with 401.
    Local development may use RECRUITER_KEY or leave recruiter routes open.
    """
    if REQUIRE_RECRUITER_AUTH and not RECRUITER_ACCOUNTS:
        raise HTTPException(
            status_code=500,
            detail="Recruiter authentication is not configured.",
        )
    if not REQUIRE_RECRUITER_AUTH and not RECRUITER_ACCOUNTS and not RECRUITER_KEY:
        return "local-recruiter"
    recruiter_id = _recruiter_id_from_credentials(x_recruiter_key, authorization)
    if not recruiter_id:
        raise HTTPException(status_code=401, detail="Recruiter sign-in is required.")
    return recruiter_id


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
    authorization: str | None = None,
) -> None:
    if _valid_token_hash(get_passport_owner_token_hash(passport_id), token):
        return
    if _recruiter_id_from_credentials(recruiter_key, authorization):
        return
    raise HTTPException(status_code=404, detail="Passport not found.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "skill-passport"}


@app.get("/ready")
def readiness() -> dict[str, str]:
    try:
        if config.APP_ENV != "development" and not (config.GITHUB_TOKEN and config.GEMINI_API_KEY):
            raise RuntimeError("Required integration configuration is missing.")
        check_storage_ready()
    except Exception as exc:
        logging.getLogger(__name__).warning("Readiness check failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=503, detail="Service is not ready.") from exc
    return {"status": "ready", "service": "skill-passport"}


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


@app.get("/features")
def features() -> dict[str, Any]:
    """Optional features the frontend shows only when enabled."""
    return {
        "custom_roles": config.ENABLE_CUSTOM_ROLES,
        "reports": config.ENABLE_REPORTS,
        "sql_depth": config.ENABLE_SQL_DEPTH,
        "skill_depth": config.ENABLE_SKILL_DEPTH,
        "skill_depth_profiles": (
            (["sql"] if config.ENABLE_SQL_DEPTH else [])
            + ["python", "react", "fastapi", "docker", "git", "unit-testing", "scikit-learn"]
            if config.ENABLE_SKILL_DEPTH else []
        ),
    }


class JobDescriptionBody(BaseModel):
    title: str = Field(max_length=1000)
    description: str = Field(max_length=20000)


@app.post("/roles/from-description", status_code=201)
def role_from_description(body: JobDescriptionBody, request: Request) -> dict:
    """A target role from a pasted job description. The AI proposes requirements; code matches
    skill names, sets minimum levels by fixed rules and checks every quote against the text.
    If the AI is unavailable the role comes from exact matching only (`mode: "exact_only"`)."""
    if not config.ENABLE_CUSTOM_ROLES:
        raise HTTPException(status_code=404, detail="Not Found")
    check_rate_limit(request, "role_from_description", max_requests=10, window_seconds=600)
    try:
        role = build_custom_role(body.title, body.description)
    except JobDescriptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    save_custom_role(role)
    return {**role, "name": role["title"], "custom": True}


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
                detail=f"Total upload size across resume, certificates and reports must be {max_aggregate // (1024 * 1024)} MB or smaller.",
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
    reports: list[UploadFile] | None = File(default=None),
    portfolio_url: str | None = Form(default=None),
    codeforces_handle: str | None = Form(default=None),
) -> dict[str, str]:
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
    role: str | dict[str, Any] = target_role
    if target_role.startswith(CUSTOM_ROLE_PREFIX) and config.ENABLE_CUSTOM_ROLES:
        stored_role = get_custom_role(target_role)
        if not stored_role:
            raise HTTPException(status_code=422, detail="Unknown target role.")
        # The full definition travels with the passport, so re-check never needs roles.json.
        role = role_definition(stored_role)
    elif target_role not in role_ids | role_names:
        raise HTTPException(status_code=422, detail="Unknown target role.")
    if not config.ENABLE_REPORTS:
        reports, portfolio_url = None, None  # feature off: the inputs are ignored
    if reports and len(reports) > MAX_REPORTS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_REPORTS} project reports may be uploaded.")
    try:
        portfolio_url = validate_portfolio_url(portfolio_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if codeforces_handle:
        try:
            codeforces_handle = validate_codeforces_handle(codeforces_handle)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        codeforces_handle = None

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
            content, total_bytes = await stream_and_validate_file(
                certificate,
                max_bytes=MAX_SINGLE_FILE_BYTES,
                current_aggregate=total_bytes,
                max_aggregate=MAX_TOTAL_UPLOAD_BYTES,
                error_label="Each certificate",
            )
            if not _supported_certificate_bytes(content):
                raise HTTPException(
                    status_code=415,
                    detail="Certificates must be PDF, PNG, or JPEG files.",
                )
            cert_bytes.append(content)

        report_files = []
        for report in reports or []:
            content, total_bytes = await stream_and_validate_file(
                report,
                max_bytes=MAX_SINGLE_FILE_BYTES,
                current_aggregate=total_bytes,
                max_aggregate=MAX_TOTAL_UPLOAD_BYTES,
                error_label="Each project report",
            )
            if not _supported_certificate_bytes(content):
                raise HTTPException(
                    status_code=422,
                    detail="Project reports must be PDF, PNG, or JPEG files.",
                )
            report_files.append((safe_filename(report.filename), content))
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
        except Exception as exc:
            # Release the reserved slot; otherwise the username stays "in progress" until the lease expires.
            fail_job(job_id, f"Ownership check failed: {exc}")
            raise

    background_tasks.add_task(
        run_passport_job,
        job_id,
        github_username,
        role,
        resume_bytes,
        cert_bytes,
        is_demo,
        report_files=report_files,
        portfolio_url=portfolio_url,
        codeforces_handle=codeforces_handle,
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


_depth_backfill_lock = Lock()
_depth_backfills: set[str] = set()
_depth_backfill_attempts: dict[str, tuple[str | None, str, float]] = {}


def _run_depth_backfill(passport_id: str) -> None:
    try:
        ensure_depth_analyzers(passport_id)
    finally:
        with _depth_backfill_lock:
            _depth_backfills.discard(passport_id)


def _queue_depth_backfill(passport: dict[str, Any], background_tasks: BackgroundTasks) -> None:
    passport_id = passport["id"]
    version = passport.get("updated_at"), passport.get("created_at", "")
    now = time.monotonic()
    with _depth_backfill_lock:
        for attempted_id, attempt in list(_depth_backfill_attempts.items()):
            if now - attempt[2] >= 300:
                del _depth_backfill_attempts[attempted_id]
        previous = _depth_backfill_attempts.get(passport_id)
        if passport_id in _depth_backfills or (
            previous and previous[:2] == version and now - previous[2] < 300
        ):
            return
        _depth_backfills.add(passport_id)
        _depth_backfill_attempts[passport_id] = (*version, now)
        background_tasks.add_task(_run_depth_backfill, passport_id)


def _maybe_queue_depth_backfill(passport: dict, background_tasks: BackgroundTasks) -> None:
    if (config.ENABLE_SQL_DEPTH and passport.get("sql_depth") is None) or (
        config.ENABLE_SKILL_DEPTH and passport.get("skill_depth") is None
    ):
        _queue_depth_backfill(passport, background_tasks)


def _stored_passport(
    passport_id: str,
    background_tasks: BackgroundTasks,
    *,
    queue_backfill: bool = True,
) -> dict:
    result = get_passport(passport_id)
    if not result:
        raise HTTPException(status_code=404, detail="Passport not found.")
    if queue_backfill:
        _maybe_queue_depth_backfill(result, background_tasks)
    return result


@app.get("/passport/{passport_id}")
def passport(passport_id: str, background_tasks: BackgroundTasks) -> dict:
    return student_view(_stored_passport(passport_id, background_tasks), list_review_requests(passport_id))


@app.get("/passport/{passport_id}/review", dependencies=[Depends(require_recruiter)])
def review_passport(passport_id: str, background_tasks: BackgroundTasks) -> dict:
    return review_view(_stored_passport(passport_id, background_tasks))


@app.get("/passports", dependencies=[Depends(require_recruiter)])
def passports(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)) -> dict:
    items, total = list_passports_page(limit, offset)
    all_requests = get_review_requests_by_passports([item["id"] for item in items])
    return {
        "items": [student_view(item, all_requests.get(item["id"], [])) for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/passports/review", dependencies=[Depends(require_recruiter)])
def review_passports(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)) -> dict:
    items, total = list_latest_passports_page(limit, offset)
    all_requests = get_review_requests_by_passports([item["id"] for item in items])
    return {
        "items": [
            {
                **_without_disabled_features(item),
                "check_labels": CHECK_LABELS,
                "review_requests": all_requests.get(item["id"], []),
                "requirement_packets": requirement_packets(item),
            }
            for item in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


class ReviewRequestBody(BaseModel):
    citation_key: str = Field(min_length=1, max_length=600)
    note: str = Field(default="", max_length=1000)


@app.post("/passport/{passport_id}/review-requests", status_code=201)
def create_passport_review_request(
    passport_id: str,
    body: ReviewRequestBody,
    background_tasks: BackgroundTasks,
    x_passport_token: str | None = Header(default=None),
) -> dict:
    """A student asks for a human look at an excluded citation. Never changes levels or exclusions."""
    _require_passport_access(passport_id, x_passport_token)
    stored = _stored_passport(passport_id, background_tasks, queue_backfill=False)
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
    _maybe_queue_depth_backfill(stored, background_tasks)
    return result


@app.post("/review-requests/{request_id}/mark-reviewed")
def mark_reviewed(request_id: str, reviewer_id: str = Depends(require_recruiter)) -> dict:
    result = mark_review_request_reviewed(request_id, reviewer_id)
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
    authorization: str | None = Header(default=None),
) -> dict:
    _require_passport_access(passport_id, x_passport_token, x_recruiter_key, authorization)
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


class RecruiterLoginBody(BaseModel):
    key: str = Field(min_length=1, max_length=512)


def _recruiter_auth_open() -> bool:
    """Development only: no recruiter keys at all, so recruiter routes are open (see require_recruiter)."""
    return not REQUIRE_RECRUITER_AUTH and not RECRUITER_ACCOUNTS and not RECRUITER_KEY


@app.post("/auth/recruiter/login")
def recruiter_login(body: RecruiterLoginBody, request: Request) -> dict:
    """Exchange a recruiter key for a short-lived sign-in token (only its hash is stored)."""
    check_rate_limit(request, "recruiter_login", max_requests=10, window_seconds=600)
    if REQUIRE_RECRUITER_AUTH and not RECRUITER_ACCOUNTS:
        raise HTTPException(status_code=500, detail="Recruiter authentication is not configured.")
    recruiter_id = _recruiter_id_for_key(body.key.strip())
    if not recruiter_id:
        raise HTTPException(status_code=401, detail="That recruiter password is not valid.")
    token = secrets.token_urlsafe(32)
    expires_at = create_recruiter_session(
        recruiter_id, hashlib.sha256(token.encode()).hexdigest(), RECRUITER_SESSION_SECONDS
    )
    return {"token": token, "role": "recruiter", "recruiter_id": recruiter_id, "expires_at": expires_at}


@app.get("/auth/me")
def auth_me(
    x_recruiter_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """Who is signed in. `role` is null when nobody is (200, so the sign-in probe isn't an error)."""
    try:
        recruiter_id = require_recruiter(x_recruiter_key, authorization)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return {"role": None, "recruiter_id": None, "auth_required": True}
    return {"role": "recruiter", "recruiter_id": recruiter_id, "auth_required": not _recruiter_auth_open()}


@app.post("/auth/logout")
def recruiter_logout(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer_token(authorization)
    if token:
        delete_recruiter_session(hashlib.sha256(token.encode()).hexdigest())
    return {"signed_out": True}


# Recruiter interview sessions. Every route needs a recruiter credential, and a session is only
# visible to the recruiter who created it (404 otherwise, so its existence isn't revealed).
# Nothing here is part of the passport payload or any student response.

def _owned_session(session_id: str, recruiter_id: str) -> dict[str, Any]:
    session = get_interview_session(session_id)
    if not session or session["recruiter_id"] != recruiter_id:
        raise HTTPException(status_code=404, detail="Interview session not found.")
    return session


def _session_response(session: dict[str, Any]) -> dict[str, Any]:
    return session_view(session, get_passport(session["passport_id"]))


@app.get("/passport/{passport_id}/interview-sessions")
def interview_sessions(passport_id: str, recruiter_id: str = Depends(require_recruiter)) -> dict:
    passport = get_passport(passport_id)
    if not passport:
        raise HTTPException(status_code=404, detail="Passport not found.")
    return {"items": [session_view(item, passport) for item in list_interview_sessions(passport_id, recruiter_id)]}


@app.post("/passport/{passport_id}/interview-sessions", status_code=201)
def create_interview_session(
    passport_id: str,
    request: Request,
    response: Response,
    recruiter_id: str = Depends(require_recruiter),
    idempotency_key: str | None = Header(default=None, max_length=100),
) -> dict:
    """Generate and save a fresh private interview set without rebuilding the passport.

    Retrying with the same Idempotency-Key returns the same session instead of a second set.
    A failed generation is saved as failed (no questions) and returned as 503; retry with a new key.
    """
    passport = get_passport(passport_id)
    if not passport:
        raise HTTPException(status_code=404, detail="Passport not found.")
    check_rate_limit(request, "interview_session", max_requests=12, window_seconds=600)
    key = (idempotency_key or "").strip() or str(uuid.uuid4())
    session, outcome = start_interview_session(passport_id, recruiter_id, key, source_revision(passport))
    if outcome == "busy" or (outcome == "existing" and session["status"] == "generating"):
        raise HTTPException(status_code=409, detail="An interview set for this candidate is still being generated.")
    if outcome == "existing":
        response.status_code = 200
        if session["status"] == "failed":
            raise HTTPException(status_code=503, detail=session.get("error") or "Interview generation failed.")
        return _session_response(get_interview_session(session["id"]) or session)
    previous = [
        question["question"]
        for item in list_interview_sessions(passport_id, recruiter_id)
        for question in item.get("questions", [])
    ]
    # Renew the reservation while generation runs: storage's interruption window counts from
    # the last heartbeat, so a slow-but-alive request is never reported as failed or replaced.
    heartbeat_stop = threading.Event()

    def renew_reservation() -> None:
        while not heartbeat_stop.wait(INTERVIEW_SESSION_HEARTBEAT_SECONDS):
            try:
                touch_interview_session(session["id"])
            except Exception:  # a storage hiccup ends renewal, not the generation itself
                return

    heartbeat = threading.Thread(target=renew_reservation, daemon=True)
    heartbeat.start()
    try:
        payload, questions = build_interview_set(passport, previous)
        complete_interview_session(session["id"], payload, questions)
    except InterviewGenerationError as exc:
        fail_interview_session(session["id"], str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).error("Interview generation failed: %s", exc.__class__.__name__)
        fail_interview_session(session["id"], "Interview generation failed unexpectedly. Try again.")
        raise HTTPException(status_code=500, detail="Interview generation failed unexpectedly. Try again.") from exc
    finally:
        heartbeat_stop.set()
    return _session_response(_owned_session(session["id"], recruiter_id))


@app.get("/interview-sessions/{session_id}")
def interview_session(session_id: str, recruiter_id: str = Depends(require_recruiter)) -> dict:
    return _session_response(_owned_session(session_id, recruiter_id))


@app.get("/interview-sessions/{session_id}/summary")
def interview_session_summary(session_id: str, recruiter_id: str = Depends(require_recruiter)) -> dict:
    return _session_response(_owned_session(session_id, recruiter_id))["summary"]


class AssessmentBody(BaseModel):
    outcome: str = Field(min_length=1, max_length=40)
    note: str = Field(default="", max_length=500)


@app.put("/interview-sessions/{session_id}/questions/{question_id}/assessment")
def assess_interview_question(
    session_id: str,
    question_id: str,
    body: AssessmentBody,
    recruiter_id: str = Depends(require_recruiter),
) -> dict:
    """Record a human observation. Never changes levels, role match, depth scores or verification."""
    session = _owned_session(session_id, recruiter_id)
    if session["status"] != "ready":
        raise HTTPException(status_code=409, detail="Only a ready interview set can be assessed.")
    if body.outcome not in OUTCOMES:
        raise HTTPException(status_code=422, detail=f"Outcome must be one of: {', '.join(OUTCOMES)}.")
    assessment = save_interview_assessment(
        session_id, question_id, body.outcome, body.note.strip(), recruiter_id
    )
    if assessment is None:
        raise HTTPException(status_code=404, detail="Question not found in this interview session.")
    view = _session_response(_owned_session(session_id, recruiter_id))
    return {"assessment": assessment, "summary": view["summary"]}
