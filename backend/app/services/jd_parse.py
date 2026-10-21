"""Custom target roles from a pasted job description.

Gemini proposes requirements (skill id, importance, quote) and the importance of skills found by
exact name. Code matches skill names and aliases, sets every minimum level by fixed rules, and
checks every quote against the description. The description itself is never stored.
"""
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.claim_validation import validate_proposals
from app.services.framework import get_skills
from app.services.gemini import GeminiOutputError, propose_json_items
from app.services.resume_parse import find_skill_mentions
from app.services.verifier import LEVEL_ORDER

TITLE_MAX_CHARS = 120
DESCRIPTION_MIN_CHARS = 50
DESCRIPTION_MAX_CHARS = 8000
MAX_REQUIREMENTS = 12
QUOTE_CONTEXT_CHARS = 60
CUSTOM_ROLE_PREFIX = "custom-"
IMPORTANCE_WORDS = re.compile(r"\b(?:required|must|essential|strong)\b", re.IGNORECASE)
TESTING_WORDS = re.compile(r"\b(?:tests?|tested|testing|unit tests?|tdd|pytest|jest)\b", re.IGNORECASE)
# Recruiter contact details are not needed to read requirements (I11 applies to every LLM call).
CONTACT_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)"),
)


class JobDescriptionError(ValueError):
    """The title or description can't be turned into a role; the message is shown to the user."""


def default_importance(quote: str) -> str:
    """Used when Gemini gives no importance: "must" only when the quote says so."""
    return "must" if IMPORTANCE_WORDS.search(quote or "") else "nice"


def min_level_for(importance: str, quote: str) -> str:
    """Fixed rule, never the AI: nice -> Used; must -> Applied; must + a testing word -> Demonstrated."""
    if importance != "must":
        return "Used"
    return "Demonstrated" if TESTING_WORDS.search(quote or "") else "Applied"


def _strictness(item: dict[str, Any]) -> tuple[bool, int]:
    return item["importance"] == "must", LEVEL_ORDER[item["min_level"]]


def _redact_contacts(text: str) -> str:
    for pattern in CONTACT_PATTERNS:
        text = pattern.sub("[REDACTED CONTACT]", text)
    return text


def _quote_around(line: str, span: tuple[int, int]) -> str:
    """The matched words plus a little context, cut at word boundaries."""
    start = max(0, span[0] - QUOTE_CONTEXT_CHARS)
    end = min(len(line), span[1] + QUOTE_CONTEXT_CHARS)
    while 0 < start < span[0] and not line[start - 1].isspace():
        start += 1
    while span[1] < end < len(line) and not line[end].isspace():
        end -= 1
    return line[start:end].strip()


def _prompt(text: str, exact_ids: list[str]) -> str:
    skills = [{"id": skill["id"], "name": skill["name"]} for skill in get_skills()]
    return (
        "Read this job description and list its technical skill requirements. Return JSON only: "
        '{"requirements":[{"skill_id":"...","importance":"must"|"nice","quote":"..."}]}. '
        "Use only the allowed skill ids. importance is \"must\" when the description presents the "
        "skill as required and \"nice\" when it is preferred, a plus or a bonus. quote is a short "
        "phrase copied exactly from the description that shows the requirement. Include skills "
        "described in other words (for example \"containerised deployments\" for docker), and "
        "give an entry for each skill already found by name so its importance is known. Never "
        "list a skill the description does not ask for. You do not set minimum levels.\n"
        f"Allowed skills: {json.dumps(skills)}\n"
        f"Skills already found by name: {json.dumps(exact_ids)}\n"
        f"Job description:\n{text}"
    )


def validate_input(title: str, description: str) -> tuple[str, str]:
    title = (title or "").strip()
    description = (description or "").strip()
    if not 1 <= len(title) <= TITLE_MAX_CHARS:
        raise JobDescriptionError(f"The job title must be 1-{TITLE_MAX_CHARS} characters.")
    if not DESCRIPTION_MIN_CHARS <= len(description) <= DESCRIPTION_MAX_CHARS:
        raise JobDescriptionError(
            f"The job description must be {DESCRIPTION_MIN_CHARS}-{DESCRIPTION_MAX_CHARS} characters "
            f"(it has {len(description)})."
        )
    return title, description


def extract_requirements(title: str, description: str) -> dict[str, Any]:
    """Validated requirements and the phrases that could not be mapped.

    Raises JobDescriptionError for bad input or no must-have requirement, and
    GeminiUnavailableError when Gemini can't be reached.
    """
    title, description = validate_input(title, description)
    text = _redact_contacts(description)
    exact = {
        item["skill_id"]: _quote_around(item["line"], item["span"])
        for item in find_skill_mentions(text)
    }
    note = None
    try:
        proposed = propose_json_items(_prompt(text, sorted(exact)), "requirements", "jd")
    except GeminiOutputError:
        proposed = []
        note = "AI suggestions were unusable this time, so only skills named exactly were matched."

    # Gemini's importance for skills found by exact name; the exact quote always wins.
    ai_importance: dict[str, str] = {}
    for item in proposed:
        if isinstance(item, dict) and item.get("skill_id") in exact and item.get("importance") in ("must", "nice"):
            if ai_importance.get(item["skill_id"]) != "must":
                ai_importance[item["skill_id"]] = item["importance"]
    candidates: list[Any] = [
        {"skill_id": skill_id, "quote": quote, "importance": ai_importance.get(skill_id), "source": "exact"}
        for skill_id, quote in exact.items()
    ]
    candidates += [
        {"skill_id": item.get("skill_id"), "quote": item.get("quote"), "importance": item.get("importance"), "source": "ai"}
        if isinstance(item, dict) else item
        for item in proposed
        if not (isinstance(item, dict) and item.get("skill_id") in exact)
    ]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        quote = item["quote"] if isinstance(item.get("quote"), str) else ""
        if item.get("importance") not in ("must", "nice"):
            item["importance"] = default_importance(quote)
        item["min_level"] = min_level_for(item["importance"], quote)

    kept, dropped = validate_proposals(
        candidates,
        text,
        MAX_REQUIREMENTS,
        prefer=lambda new, old: _strictness(new) > _strictness(old),
        sort_key=lambda item: (item["importance"] != "must", -LEVEL_ORDER[item["min_level"]]),
    )
    names = {skill["id"]: skill["name"] for skill in get_skills()}
    requirements = [
        {
            "skill_id": item["skill_id"],
            "name": names[item["skill_id"]],
            "importance": item["importance"],
            "min_level": item["min_level"],
            "quote": item["quote"],
            "source": item["source"],
        }
        for item in kept
    ]
    if not requirements:
        raise JobDescriptionError(
            "No skills from the skill framework were found in this job description."
        )
    if not any(item["importance"] == "must" for item in requirements):
        raise JobDescriptionError(
            "No must-have requirement was found. Say which skills are required "
            "(for example \"Python is required\") and try again."
        )
    return {"title": title, "requirements": requirements, "dropped": dropped, "note": note}


def build_custom_role(title: str, description: str) -> dict[str, Any]:
    extracted = extract_requirements(title, description)
    return {
        "id": f"{CUSTOM_ROLE_PREFIX}{uuid.uuid4()}",
        "title": extracted["title"],
        "requirements": extracted["requirements"],
        "dropped": extracted["dropped"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": extracted["note"],
    }


def role_definition(stored: dict[str, Any]) -> dict[str, Any]:
    """The role object match_role takes (same shape as roles.json), kept on the passport."""
    return {
        "id": stored["id"],
        "name": stored["title"],
        "title": stored["title"],
        "custom": True,
        "requirements": [
            {key: item[key] for key in ("skill_id", "name", "importance", "min_level", "quote", "source") if key in item}
            for item in stored["requirements"]
        ],
    }
