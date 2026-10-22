"""Custom target roles from a pasted job description.

Gemini proposes requirements (skill id, importance, quote) and the importance of skills found by
exact name. Code matches skill names and aliases, sets every minimum level by fixed rules, and
checks every quote against the description. The description itself is never stored.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.claim_validation import validate_proposals
from app.services.framework import get_skills
from app.services.gemini import GeminiOutputError, GeminiUnavailableError, propose_json_items
from app.services.resume_parse import find_skill_mentions
from app.services.verifier import LEVEL_ORDER

TITLE_MAX_CHARS = 120
DESCRIPTION_MIN_CHARS = 50
DESCRIPTION_MAX_CHARS = 8000
MAX_REQUIREMENTS = 12
QUOTE_CONTEXT_CHARS = 60
CUSTOM_ROLE_PREFIX = "custom-"
EXACT_ONLY_NOTE = "AI suggestions unavailable right now; showing requirements found by exact matching."
logger = logging.getLogger(__name__)
IMPORTANCE_WORDS = re.compile(r"\b(?:required|must|essential|strong)\b", re.IGNORECASE)
TESTING_WORDS = re.compile(r"\b(?:tests?|tested|testing|unit tests?|tdd|pytest|jest)\b", re.IGNORECASE)
# A quote stays inside its sentence: ; ! ? always end one, "." only before whitespace or the
# end (so "Node.js" and "Python 3.11" are not cut). Line breaks end it too (one bullet each).
SENTENCE_END = re.compile(r"[;!?]|\.(?=\s|$)")
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
    """The matched words plus a little context from the same sentence and line.

    The quote decides the minimum level (a testing word makes a must-have Demonstrated), so it
    must not borrow words from a neighbouring sentence or bullet.
    """
    start = line.rfind("\n", 0, span[0]) + 1
    end = line.find("\n", span[1])
    if end == -1:
        end = len(line)
    for boundary in SENTENCE_END.finditer(line, start, span[0]):
        start = boundary.end()
    boundary = SENTENCE_END.search(line, span[1], end)
    if boundary:
        end = boundary.end()
    # Long sentences: keep up to QUOTE_CONTEXT_CHARS each side, cut at word boundaries.
    if span[0] - start > QUOTE_CONTEXT_CHARS:
        start = span[0] - QUOTE_CONTEXT_CHARS
        while start < span[0] and not line[start - 1].isspace():
            start += 1
    if end - span[1] > QUOTE_CONTEXT_CHARS:
        end = span[1] + QUOTE_CONTEXT_CHARS
        while end > span[1] and not line[end].isspace():
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

    When Gemini can't help (unavailable after its retries, or invalid JSON after one retry), the
    result comes from exact matching only (`mode: "exact_only"`) with the same keyword importance
    rule, level rules, validation, dedupe and cap. Raises JobDescriptionError for bad input or
    when there is no must-have requirement.
    """
    title, description = validate_input(title, description)
    text = _redact_contacts(description)
    exact = {
        item["skill_id"]: _quote_around(item["line"], item["span"])
        for item in find_skill_mentions(text)
    }
    mode, note = "ai", None
    try:
        proposed = propose_json_items(_prompt(text, sorted(exact)), "requirements", "jd")
    except (GeminiUnavailableError, GeminiOutputError) as exc:
        logger.warning("Job description: AI suggestions unavailable (%s); using exact matches only", exc)
        proposed = []
        mode, note = "exact_only", EXACT_ONLY_NOTE

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
    return {"title": title, "requirements": requirements, "dropped": dropped, "mode": mode, "note": note}


def build_custom_role(title: str, description: str) -> dict[str, Any]:
    extracted = extract_requirements(title, description)
    return {
        "id": f"{CUSTOM_ROLE_PREFIX}{uuid.uuid4()}",
        "title": extracted["title"],
        "requirements": extracted["requirements"],
        "dropped": extracted["dropped"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": extracted["mode"],
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
