"""Custom target roles from a pasted job description.

Code matches skill names and aliases and decides their quote, importance and minimum level by
fixed rules. Gemini only proposes skills described in other words (skill id, importance, quote);
every quote is checked against the description and every minimum level is set by fixed rules.
The description itself is never stored.
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
from app.services.resume_parse import COMMON_WORDS, skill_terms, term_span
from app.services.verifier import LEVEL_ORDER, reachable_min_level

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
# Exact-only fallback: words in a line that mark it nice-to-have, and section headings.
NICE_WORDS = re.compile(
    r"\b(?:nice[ -]to[ -]have|plus|bonus|preferred|familiarity|exposure)\b", re.IGNORECASE
)
HEADING_SECTIONS = (  # checked in this order: "Preferred qualifications" is nice, not requirements
    ("nice", ("nice to have", "nice-to-have", "bonus", "preferred")),
    ("requirements", (
        "requirements", "must have", "must-have", "must haves", "must-haves", "qualifications",
        "minimum qualifications", "what you need", "what you'll need", "what you will need",
    )),
    ("responsibilities", (
        "what you will do", "what you'll do", "responsibilities", "key responsibilities", "you will",
    )),
)
# Lower is stronger. An explicit nice-to-have signal outranks a responsibilities line, so a
# skill listed under "Nice to have" stays nice even if a duty also mentions it.
LINE_RANK = {"must_words": 0, "requirements": 1, "nice": 2, "responsibilities": 3, "other": 4}
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


def min_level_for(importance: str, quote: str, skill_id: str = "") -> str:
    """Fixed rule, never the AI: nice -> Used; must -> Applied; must + a testing word -> Demonstrated,
    capped at the highest level the skill can reach (Unit Testing: Applied)."""
    if importance != "must":
        return "Used"
    level = "Demonstrated" if TESTING_WORDS.search(quote or "") else "Applied"
    return reachable_min_level(skill_id, level)


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


def _heading_section(line: str) -> str | None | bool:
    """The section a heading line starts ("nice", "requirements", "responsibilities"), None for
    any other heading (e.g. "About the role"), or False when the line is not a heading."""
    stripped = line.strip()
    if (
        not stripped or stripped[0] in "-*•·–" or len(stripped) > 60
        or stripped.endswith((".", ";", "!", "?")) or len(stripped.rstrip(":").split()) > 6
    ):
        return False
    key = " ".join(stripped.rstrip(":").replace("’", "'").lower().split())
    for section, phrases in HEADING_SECTIONS:
        if any(key == phrase or key.startswith(phrase + " ") for phrase in phrases):
            return section
    return None


def _line_signal(quote: str, section: str | None) -> tuple[int, str]:
    """(rank, importance) for one mention. Nice-words in the line beat both must-words and the
    section ("Familiarity with Docker" under Requirements is nice)."""
    if NICE_WORDS.search(quote):
        return LINE_RANK["nice"], "nice"
    if IMPORTANCE_WORDS.search(quote):
        return LINE_RANK["must_words"], "must"
    if section == "requirements":
        return LINE_RANK["requirements"], "must"
    if section == "nice":
        return LINE_RANK["nice"], "nice"
    if section == "responsibilities":
        return LINE_RANK["responsibilities"], "must"
    return LINE_RANK["other"], "nice"


def _sentence_spans(line: str):
    start = 0
    for boundary in SENTENCE_END.finditer(line):
        yield start, boundary.end()
        start = boundary.end()
    if start < len(line):
        yield start, len(line)


def _jd_term_span(term: str, sentence: str) -> tuple[int, int] | None:
    """Like the resume matcher, plus a simple plural ("REST APIs" for "rest api"). Not for
    everyday-word skills, where "reacts" or "shells" would be ordinary English."""
    span = term_span(term, sentence)
    if span is not None or term.lower() in COMMON_WORDS:
        return span
    for plural in (term + "s", term + "es"):
        span = term_span(plural, sentence)
        if span is not None:
            return span
    return None


LIST_SEPARATOR = re.compile(r"\s*(?:[,/&]|\band\b)\s*", re.IGNORECASE)
# A version after a skill name in a list entry: "Python 3.11", "Java 17+", "Node.js v20".
TRAILING_VERSION = re.compile(r"\s+v?\d+(?:\.\d+)*\+?$", re.IGNORECASE)
# Short unbulleted lines that state a requirement ("Experience with Python") rather than head a
# section ("Our Python stack"); such lines keep the current section.
REQUIREMENT_LEAD = re.compile(
    r"^\s*(?:experience|knowledge|proficiency|proficient|familiarity|familiar|exposure|"
    r"understanding|strong|solid|good|working|hands-on|ability|expertise|skilled|fluency|fluent)\b",
    re.IGNORECASE,
)


def _protect_separator_terms(line: str) -> tuple[str, list[str]]:
    """Replace skill names that contain a list separator ("CI/CD", "Node.js / Express") with
    placeholders, longest first, so splitting the list can't cut them apart."""
    names = sorted(
        {term for skill in get_skills() for term in skill_terms(skill) if LIST_SEPARATOR.search(term)},
        key=len, reverse=True,
    )
    kept: list[str] = []
    for name in names:
        pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)

        def placeholder(match: re.Match[str]) -> str:
            kept.append(match.group(0))
            return f"\x00{len(kept) - 1}\x00"

        line = pattern.sub(placeholder, line)
    return line, kept


def _is_skill_list_line(line: str) -> bool:
    """True when a line contains only known skill names/aliases separated as a list.

    Supports common plain-text separators (comma, slash, ampersand, and 'and') without
    mistaking a heading like "Our Python stack" for a skills list. Skill names that contain a
    separator ("CI/CD") stay whole, and an entry may end with a version ("Python 3.11").
    """
    cleaned = line.strip().rstrip(":,").strip()
    if not cleaned:
        return False
    protected, kept = _protect_separator_terms(cleaned)
    items = [
        re.sub(r"\x00(\d+)\x00", lambda match: kept[int(match.group(1))], item)
        for item in LIST_SEPARATOR.split(protected)
    ]
    for item in items:
        item = TRAILING_VERSION.sub("", item.strip())
        if not item or not any(
            (span := _jd_term_span(term, item)) == (0, len(item))
            for skill in get_skills()
            for term in skill_terms(skill)
        ):
            return False
    return True


def _is_requirement_line(line: str) -> bool:
    """A short line that states a requirement for a named skill ("Experience with Python",
    "Familiarity with Docker"), not a section heading ("Our Python stack", "About us")."""
    return bool(REQUIREMENT_LEAD.match(line)) and any(
        _jd_term_span(term, line) is not None for skill in get_skills() for term in skill_terms(skill)
    )


def strongest_exact_matches(text: str) -> dict[str, tuple[str, str]]:
    """Exact-only fallback: for each skill named in the text, the strongest sentence naming it.

    Every sentence of every line is considered; the section comes from the last heading seen.
    Returns {skill_id: (quote, importance)}; ties keep the first sentence.
    """
    best: dict[str, tuple[int, str, str]] = {}
    section: str | None = None
    for line in text.splitlines():
        heading = _heading_section(line)
        if heading is not False:
            # Skill-list content and requirement lines preserve the section. A heading like
            # "Our Python stack" mentions a skill but must still reset the section.
            if not _is_skill_list_line(line) and not _is_requirement_line(line):
                section = heading
        for start, end in _sentence_spans(line):
            sentence = line[start:end]
            for skill in get_skills():
                for term in skill_terms(skill):
                    span = _jd_term_span(term, sentence)
                    if span is None:
                        continue
                    quote = _quote_around(line, (start + span[0], start + span[1]))
                    rank, importance = _line_signal(quote, section)
                    if skill["id"] not in best or rank < best[skill["id"]][0]:
                        best[skill["id"]] = (rank, quote, importance)
                    break
    return {skill_id: (quote, importance) for skill_id, (_, quote, importance) in best.items()}


def _first_sentence(quote: str) -> str:
    """An AI quote cut to its first sentence and line, so a neighbouring sentence can't change
    the minimum level (it stays a substring of the text, so the quote check still applies)."""
    first = quote.strip().splitlines()[0] if quote.strip() else ""
    boundary = SENTENCE_END.search(first)
    return first[: boundary.end()].strip() if boundary else first.strip()


def _prompt(text: str, exact_ids: list[str]) -> str:
    skills = [{"id": skill["id"], "name": skill["name"]} for skill in get_skills()]
    return (
        "Read this job description and list its technical skill requirements. Return JSON only: "
        '{"requirements":[{"skill_id":"...","importance":"must"|"nice","quote":"..."}]}. '
        "Use only the allowed skill ids. importance is \"must\" when the description presents the "
        "skill as required and \"nice\" when it is preferred, a plus or a bonus. quote is a short "
        "phrase copied exactly from one sentence of the description that shows the requirement. "
        "List only skills described in other words (for example \"containerised deployments\" "
        "for docker); skip the skills already found by name. Never list a skill the description "
        "does not ask for. You do not set minimum levels.\n"
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

    Skills named in the text (`strongest_exact_matches`: the strongest sentence per skill, with
    simple plurals) take their quote, importance and minimum level only from fixed rules; Gemini
    cannot change them. Gemini adds only skills not named exactly (paraphrases), each validated
    (known id, quote in the text, cut to one sentence and line), with Gemini's importance and the
    fixed level rules. When Gemini can't help (unavailable after its retries, or invalid JSON
    after one retry) the exact matches alone are returned (`mode: "exact_only"`); nothing from
    that path is cached. Raises JobDescriptionError for bad input or no must-have requirement.
    """
    title, description = validate_input(title, description)
    text = _redact_contacts(description)
    exact = strongest_exact_matches(text)
    mode, note = "ai", None
    try:
        proposed = propose_json_items(_prompt(text, sorted(exact)), "requirements", "jd")
    except (GeminiUnavailableError, GeminiOutputError) as exc:
        logger.warning("Job description: AI suggestions unavailable (%s); using exact matches only", exc)
        proposed = []
        mode, note = "exact_only", EXACT_ONLY_NOTE

    candidates: list[Any] = [
        {"skill_id": skill_id, "quote": quote, "importance": importance, "source": "exact"}
        for skill_id, (quote, importance) in exact.items()
    ]
    # Gemini only adds paraphrased skills; anything it says about an exact match is ignored.
    candidates += [
        {
            "skill_id": item.get("skill_id"),
            "quote": _first_sentence(item["quote"]) if isinstance(item.get("quote"), str) else item.get("quote"),
            "importance": item.get("importance"),
            "source": "ai",
        }
        if isinstance(item, dict) else item
        for item in proposed
        if not (
            isinstance(item, dict)
            and isinstance(item.get("skill_id"), str)
            and item["skill_id"] in exact
        )
    ]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        quote = item["quote"] if isinstance(item.get("quote"), str) else ""
        if item.get("importance") not in ("must", "nice"):
            item["importance"] = default_importance(quote)
        skill_id = item["skill_id"] if isinstance(item.get("skill_id"), str) else ""
        item["min_level"] = min_level_for(item["importance"], quote, skill_id)

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
