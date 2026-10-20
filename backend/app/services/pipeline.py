import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import VERIFIER_VERSION
from app.services.claim_proposer import propose_claims
from app.services.copy_check import add_copy_signals
from app.services.coverage import build_coverage
from app.services.github_ingest import ingest_github
from app.services.growth_and_kit import generate_growth_and_kit
from app.services.jobs import complete_job, fail_job, keep_job_lease, update_job
from app.services.resume_parse import parse_certificate, parse_resume, redact_personal_data
from app.services.role_match import match_role
from app.services.storage import get_passport, save_passport
from app.services.verifier import exclude_uncounted_citations, verify_claims

logger = logging.getLogger(__name__)


def _merge_skill_matches(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    mentions: dict[str, str] = {}
    for group in groups:
        for item in group:
            mentions.setdefault(item["skill_id"], item["mention"])
    return [{"skill_id": key, "mention": value} for key, value in mentions.items()]


def _redact_mentions(items: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {**item, "mention": redact_personal_data(item.get("mention") or "", detect_name=False)}
        for item in items
    ]


def build_passport(
    github_username: str,
    target_role: str,
    resume_bytes: bytes,
    certificate_bytes: list[bytes] | None = None,
    is_demo: bool = False,
    passport_id: str | None = None,
    force_refresh: bool = False,
    resume_skill_mentions: list[dict[str, str]] | None = None,
    certificate_skill_mentions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    existing = get_passport(passport_id) if passport_id else None
    update_job_id = passport_id if passport_id and not existing else None
    if existing:
        previous_levels = {
            item["skill_id"]: item["level"] for item in existing.get("skills", [])
        }
    else:
        previous_levels = {}

    def progress(step: str) -> None:
        if update_job_id:
            update_job(update_job_id, step)

    progress("Reading GitHub")
    ingestion = ingest_github(github_username, force_refresh=force_refresh)
    progress("Reading resume")
    if resume_bytes:
        resume_skills = parse_resume(resume_bytes)["skills"]
    elif resume_skill_mentions is not None:
        resume_skills = resume_skill_mentions
    else:
        raise ValueError("A resume PDF or previously parsed resume skills are required.")
    if certificate_bytes:
        certificate_groups = [
            parse_certificate(file_bytes) for file_bytes in certificate_bytes
        ]
        certificate_skills = _merge_skill_matches(*certificate_groups)
    else:
        certificate_skills = certificate_skill_mentions or []
    # Stored mentions from older passports may predate redaction; the redaction is idempotent.
    resume_skills = _redact_mentions(resume_skills)
    certificate_skills = _redact_mentions(certificate_skills)
    progress("Proposing skills")
    claims = propose_claims(ingestion, resume_skills)
    progress("Verifying evidence")
    verified = verify_claims(
        claims,
        ingestion,
        resume_skills=resume_skills,
        certificate_skills=certificate_skills,
    )
    add_copy_signals(
        verified["skills"],
        ingestion["username"],
        {
            f"{repo['name']}/{path}": source["first_commit_date"]
            for repo in ingestion["repos"]
            for path, source in repo.get("source_files", {}).items()
            if source.get("first_commit_date")
        },
    )
    # Earlier-source matches and citations with no candidate-written lines are removed before
    # role matching, so the match sees the final levels.
    exclude_uncounted_citations(verified, ingestion["repos"])
    progress("Matching role")
    coverage = build_coverage(ingestion)
    role_match = match_role(verified["skills"], target_role, coverage)
    progress("Building growth plan")
    generation_warning = None
    try:
        generated = generate_growth_and_kit(
            verified["skills"], role_match, ingestion
        )
    except Exception as exc:  # verified evidence is still valid without the AI write-up
        logger.exception("Growth plan / interview kit generation failed")
        generated = {"growth_tasks": [], "interview_kit": [], "generation_error": exc.__class__.__name__}
    generation_error = generated.get("generation_error")
    if generation_error:
        generation_warning = (
            "The growth plan and interview kit could not be generated this time "
            f"({generation_error}). Verified evidence is unaffected; use Re-check to try again."
        )
    now = datetime.now(timezone.utc).isoformat()
    passport = {
        "id": passport_id or str(uuid.uuid4()),
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
        "github_username": ingestion["username"],
        "target_role": role_match["role_name"],
        "sources": {
            "repos": ingestion["repo_count"],
            "resume": bool(resume_bytes) or bool(existing and existing["sources"]["resume"]),
            "certificates": (
                len(certificate_bytes)
                if certificate_bytes
                else (existing or {}).get("sources", {}).get("certificates", 0)
            ),
        },
        "coverage": coverage,
        "skills": verified["skills"],
        "rejected_claims": verified["rejected_claims"],
        "role_match": role_match,
        "growth_tasks": generated["growth_tasks"],
        "interview_kit": generated["interview_kit"],
        "is_demo": is_demo or bool(existing and existing.get("is_demo")),
        "resume_skill_mentions": resume_skills,
        "certificate_skill_mentions": certificate_skills,
        "generation_warning": generation_warning,
        "generation_error": generation_error,
        # The rules this passport was built with; recheck_all.py --only-incomplete rebuilds older ones.
        "verifier_version": VERIFIER_VERSION,
    }
    if existing:
        # Union of old and new skills, after earlier sources were excluded; a skill
        # that is absent on one side counts as Claimed (no evidence).
        new_levels = {item["skill_id"]: item["level"] for item in passport["skills"]}
        passport["level_changes"] = [
            {
                "skill_id": skill_id,
                "from": previous_levels.get(skill_id, "Claimed"),
                "to": new_levels.get(skill_id, "Claimed"),
            }
            for skill_id in sorted(set(previous_levels) | set(new_levels))
            if previous_levels.get(skill_id, "Claimed") != new_levels.get(skill_id, "Claimed")
        ]
    save_passport(passport)
    if update_job_id:
        complete_job(update_job_id, passport["id"])
    return passport


def recheck_passport(existing: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a stored passport from a fresh GitHub read and its stored resume/certificate mentions."""
    return build_passport(
        github_username=existing["github_username"],
        target_role=existing["role_match"]["role_id"],
        resume_bytes=b"",
        passport_id=existing["id"],
        force_refresh=True,
        resume_skill_mentions=existing.get("resume_skill_mentions", []),
        certificate_skill_mentions=existing.get("certificate_skill_mentions", []),
    )


def run_passport_job(
    job_id: str,
    github_username: str,
    target_role: str,
    resume_bytes: bytes,
    certificate_bytes: list[bytes],
    is_demo: bool,
) -> None:
    update_job_id = job_id
    with keep_job_lease(job_id):
        try:
            build_passport(
                github_username=github_username,
                target_role=target_role,
                resume_bytes=resume_bytes,
                certificate_bytes=certificate_bytes,
                is_demo=is_demo,
                passport_id=update_job_id,
            )
        except Exception as exc:
            logger.exception("Passport job %s failed", job_id)
            fail_job(job_id, str(exc))
            raise
