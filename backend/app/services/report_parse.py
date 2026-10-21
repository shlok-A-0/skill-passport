"""Project reports and a portfolio link as extra sources.

Reports are claims: Gemini proposes skills with a short quote, code keeps only quotes found in
the report, and each claim is cross-checked against the passport's code evidence. Reports and
portfolios never raise a level. A portfolio URL is never fetched.
"""
import json
import re
from pathlib import PurePath
from typing import Any
from urllib.parse import urlsplit

from app.services.claim_validation import validate_proposals
from app.services.framework import get_skills, skill_map
from app.services.gemini import GeminiOutputError, GeminiUnavailableError, propose_json_items
from app.services.identity import RESERVED_PATHS
from app.services.resume_parse import extract_certificate_text, redact_personal_data
from app.services.verifier import counts_for_level

MAX_REPORTS = 3
MAX_REPORT_CLAIMS = 15
MAX_REPORT_CHARS = 40_000
MAX_REPO_LINKS = 20
MAX_PORTFOLIO_URL_CHARS = 300
REPO_LINK = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:www\.)?github\.com/"
    r"([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/([A-Za-z0-9._-]{1,100})",
    re.IGNORECASE,
)
EXTRACTION_FAILED = (
    "Skills could not be read from this report this time. Reports aren't stored, so upload it "
    "again with a new passport to retry."
)


def safe_filename(name: str | None) -> str:
    """The base name only, without control characters, at most 120 characters."""
    base = PurePath((name or "").replace("\\", "/")).name
    cleaned = "".join(char for char in base if char.isprintable()).strip()
    return cleaned[:120] or "report"


def repo_links(text: str) -> list[dict[str, str]]:
    """github.com/<owner>/<repo> links in the raw report text, first appearance first. No API calls."""
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in REPO_LINK.finditer(text or ""):
        owner = match.group(1)
        repo = re.sub(r"\.git$", "", match.group(2).rstrip("."), flags=re.IGNORECASE)
        key = (owner.lower(), repo.lower())
        if owner.lower() in RESERVED_PATHS or not repo or key in seen:
            continue
        seen.add(key)
        links.append({"owner": owner, "repo": repo, "url": f"https://github.com/{owner}/{repo}"})
        if len(links) >= MAX_REPO_LINKS:
            break
    return links


def _prompt(text: str) -> str:
    skills = [{"id": skill["id"], "name": skill["name"]} for skill in get_skills()]
    return (
        "Read this project report and list the technical skills it says the author used. Return "
        'JSON only: {"claims":[{"skill_id":"...","quote":"..."}]}. Use only the allowed skill ids. '
        "quote is a short phrase copied exactly from the report that mentions the skill's use. "
        "Never list a skill the report does not mention. These are claims to check, not proof.\n"
        f"Allowed skills: {json.dumps(skills)}\n"
        f"Report:\n{text}"
    )


def parse_report(filename: str | None, file_bytes: bytes) -> dict[str, Any]:
    """Validated skill claims and repository links from one report. Its text is not kept.

    Unreadable files raise ValueError/RuntimeError like certificates. If Gemini is unavailable
    the report is kept with no claims and an `error` note.
    """
    raw_text = extract_certificate_text(file_bytes)
    links = repo_links(raw_text)  # from the raw text: redaction removes "GitHub: ..." lines
    text = redact_personal_data(raw_text)[:MAX_REPORT_CHARS]  # I11 before the Gemini call
    error = None
    try:
        proposed = propose_json_items(_prompt(text), "claims", "report")
    except (GeminiUnavailableError, GeminiOutputError):
        proposed = []
        error = EXTRACTION_FAILED
    kept, dropped = validate_proposals(proposed, text, MAX_REPORT_CLAIMS)
    return {
        "filename": safe_filename(filename),
        "claims": [{"skill_id": item["skill_id"], "quote": item["quote"]} for item in kept],
        "dropped": dropped,
        "repo_links": links,
        **({"error": error} if error else {}),
    }


EVIDENCE_RANK = {"code": 0, "commit": 1, "config": 2, "dependency": 3}


def strongest_evidence(skill: dict[str, Any]) -> dict[str, Any] | None:
    """A pointer to the skill's strongest counted evidence item, or None if nothing counts."""
    counted = [
        item for item in skill.get("evidence", [])
        if counts_for_level(skill["skill_id"], item)
    ]
    if skill.get("level", "Claimed") == "Claimed" or not counted:
        return None
    best = min(
        counted,
        key=lambda item: (
            not item.get("demonstrated_by"),
            EVIDENCE_RANK.get(item.get("type"), 9),
        ),
    )
    return {key: best.get(key) for key in ("type", "repo", "file_path", "start_line", "end_line", "url")}


def report_results(
    reports: list[dict[str, Any]], skills: list[dict[str, Any]], github_username: str
) -> list[dict[str, Any]]:
    """Each report claim checked against the final (post-exclusion) evidence; levels untouched.

    Works for freshly parsed reports and for stored ones on re-check (statuses are recomputed).
    """
    by_skill = {skill["skill_id"]: skill for skill in skills}
    names = skill_map()
    results = []
    for report in reports:
        claims = []
        for claim in report.get("claims", []):
            skill = by_skill.get(claim["skill_id"])
            pointer = strongest_evidence(skill) if skill else None
            claims.append({
                "skill_id": claim["skill_id"],
                "name": (names.get(claim["skill_id"]) or {}).get("name", claim["skill_id"]),
                "quote": claim["quote"],
                "status": "corroborated" if pointer else "not_evidenced",
                **({"evidence": pointer} if pointer else {}),
            })
        results.append({
            "filename": report.get("filename") or "report",
            "claims": claims,
            "dropped": report.get("dropped", []),
            "repo_links": [
                {
                    "owner": link["owner"],
                    "repo": link["repo"],
                    "url": link["url"],
                    "status": "own" if link["owner"].lower() == github_username.lower() else "other_account",
                }
                for link in report.get("repo_links", [])
            ],
            **({"error": report["error"]} if report.get("error") else {}),
        })
    return results


def add_report_mentions(skills: list[dict[str, Any]], reports: list[dict[str, Any]]) -> None:
    """Not-evidenced report claims become unverified (Claimed) mentions, like resume mentions.

    They never add evidence or change a level, so they never satisfy a requirement.
    """
    by_skill = {skill["skill_id"]: skill for skill in skills}
    names = skill_map()
    for report in reports:
        for claim in report["claims"]:
            if claim["status"] != "not_evidenced":
                continue
            skill = by_skill.get(claim["skill_id"])
            if skill is None:
                skill = {
                    "skill_id": claim["skill_id"],
                    "name": (names.get(claim["skill_id"]) or {}).get("name", claim["skill_id"]),
                    "level": "Claimed",
                    "certified": False,
                    "evidence": [],
                }
                skills.append(skill)
                by_skill[claim["skill_id"]] = skill
            skill.setdefault("report_mention", claim["quote"])


def validate_portfolio_url(value: str | None) -> str | None:
    """An https URL of at most 300 characters, or None when empty. Raises ValueError otherwise."""
    url = (value or "").strip()
    if not url:
        return None
    if len(url) > MAX_PORTFOLIO_URL_CHARS or any(char.isspace() for char in url):
        raise ValueError(f"Portfolio URL must be a single https:// link of at most {MAX_PORTFOLIO_URL_CHARS} characters.")
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError as exc:
        raise ValueError("Portfolio URL is not a valid link.") from exc
    if parts.scheme.lower() != "https" or not host or parts.username or parts.password:
        raise ValueError("Portfolio URL must start with https:// and name a website.")
    return url


def portfolio_status(url: str, github_username: str) -> dict[str, str]:
    """Ownership only for <username>.github.io; any other site is never fetched or checked."""
    host = (urlsplit(url).hostname or "").lower()
    own = host == f"{github_username.lower()}.github.io"
    return {"url": url, "status": "own_github_pages" if own else "external_unverified"}
