import copy
import hmac
import logging
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi import BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import FRONTEND_ORIGINS, RECRUITER_KEY
from app.routes.debug import router as debug_router
from app.services.badge import create_badge
from app.services.framework import get_roles, get_skills
from app.services.github_ingest import GitHubIngestError
from app.services.jobs import create_job, get_job
from app.services.pipeline import build_passport, run_passport_job
from app.services.storage import get_passport, list_passports

app = FastAPI(title="Skill Passport", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(debug_router)


def student_view(passport: dict[str, Any]) -> dict[str, Any]:
    """The passport without fraud signals: showing them to the person being checked
    would teach them how to get around the checks."""
    view = copy.deepcopy(passport)
    for skill in view.get("skills", []):
        for item in skill.get("evidence", []):
            item.pop("integrity", None)
    for entry in view.get("interview_kit", []):
        for question in entry.get("questions", []):
            question.pop("good_answer_mentions", None)
    rejected = view.get("rejected_claims", [])
    copied = sum(1 for item in rejected if item.get("kind") == "copied")
    view["rejected_claims"] = [item for item in rejected if item.get("kind") != "copied"]
    if copied:
        noun = "citation was" if copied == 1 else "citations were"
        view["rejected_claims"].append({
            "kind": "review",
            "count": copied,
            "reason": f"{copied} {noun} not counted after authorship review.",
        })
    return view


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
    return student_view(_stored_passport(passport_id))


@app.get("/passport/{passport_id}/review", dependencies=[Depends(require_recruiter)])
def review_passport(passport_id: str) -> dict:
    return _stored_passport(passport_id)


@app.get("/passports")
def passports() -> list[dict]:
    return [student_view(item) for item in list_passports()]


@app.get("/passports/review", dependencies=[Depends(require_recruiter)])
def review_passports() -> list[dict]:
    return list_passports()


@app.post("/passport/{passport_id}/recheck")
def recheck_passport(passport_id: str) -> dict:
    existing = get_passport(passport_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Passport not found.")
    try:
        result = build_passport(
            github_username=existing["github_username"],
            target_role=existing["role_match"]["role_id"],
            resume_bytes=b"",
            passport_id=passport_id,
            force_refresh=True,
            resume_skill_mentions=existing.get("resume_skill_mentions", []),
            certificate_skill_mentions=existing.get("certificate_skill_mentions", []),
        )
    except Exception as exc:
        logging.getLogger(__name__).exception("Passport re-check failed: %s", passport_id)
        raise HTTPException(status_code=502, detail=f"Re-check failed: {exc}") from exc
    return {
        "passport": student_view(result),
        "level_changes": result.get("level_changes", []),
    }


@app.get("/passport/{passport_id}/badge")
def badge(passport_id: str) -> dict:
    result = get_passport(passport_id)
    if not result:
        raise HTTPException(status_code=404, detail="Passport not found.")
    return create_badge(result)
