"""Private recruiter interview sessions (I7, recruiter side).

The student practice kit (`passport["interview_kit"]`, built with the growth plan) is unchanged.
This module builds a separate, recruiter-only set on demand:

1. `select_interview_targets()` picks evidence deterministically: job-critical requirements whose
   evidence needs an attribution review first, then job-critical requirements with incomplete
   evidence, then other attribution concerns, then strong evidence for deeper questions.
2. Gemini writes questions for the selected code citations only. It never chooses the citation:
   each question names an anchor we supplied, and `validate_question()` checks the skill, file,
   line range and that the question refers to the cited code before anything is saved.
3. Requirements without a code citation get a fixed recruiter-led discussion prompt, never an
   invented citation.

Interview outcomes are human observations stored separately; nothing here reads or changes
levels, role matching, depth scores or verification.
"""
import json
import re
import uuid
from typing import Any, Literal

from google import genai
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app import config
from app.services.framework import skill_map
from app.services.gemini import generate_json
from app.services.packets import ATTRIBUTION_WARNINGS, MEDIUM_CAPS, _cap_reason, attribution_details
from app.services.verifier import EARLIER_SOURCE_KINDS, line_share, snippet_sha256

PROMPT_VERSION = "interview-set-1"
QUESTION_TYPES = ("explain", "design_tradeoff", "failure_case", "modification")
QUESTION_TYPE_LABELS = {
    "explain": "Explain",
    "design_tradeoff": "Design trade-off",
    "failure_case": "Failure case",
    "modification": "Modification",
    "discussion": "Recruiter-led discussion",
}
OUTCOMES = {
    "explained_well": "Explained well",
    "partly_explained": "Partly explained",
    "could_not_explain": "Couldn't explain",
}
MAX_CODE_TARGETS = 4
MAX_DISCUSSION_TARGETS = 2
MAX_TARGETS = 5
MAX_QUESTIONS_PER_ANCHOR = 2
MAX_SNIPPET_LINES = 80
MAX_PREVIOUS_QUESTIONS = 20
STRONG_LEVELS = {"Demonstrated", "Applied"}
CONCERN_LABELS = {"Medium", "Low", "Low (limited history)"}

REASON_MUST = "Job-critical requirement"
REASON_NICE = "Job requirement (nice to have)"
REASON_ATTRIBUTION = "Attribution needs review"
REASON_STRONG = "Strong evidence selected for deeper questioning"
REASON_NO_CODE = (
    "No code citation for this requirement in the analysed files, so this is a recruiter-led "
    "discussion. Missing evidence is not evidence of a missing skill."
)
DISCUSSION_GUIDANCE = [
    "There is no cited code to compare against: listen for a concrete project, the decisions made and why.",
    "A strong answer points to something checkable afterwards (a repository, file or commit).",
    "A vague answer is a prompt for follow-up, not proof the candidate lacks the skill.",
]
# Common words that say nothing about whether a question refers to the cited code.
_STOP_WORDS = {
    "and", "the", "for", "not", "def", "class", "return", "import", "from", "self", "none", "true",
    "false", "const", "let", "var", "function", "this", "else", "elif", "while", "with", "async",
    "await", "try", "except", "finally", "raise", "pass", "print", "string", "int", "str", "list",
    "dict", "new", "public", "private", "static", "void", "null", "undefined", "export", "default",
    "type", "value", "data", "result", "error", "code", "file", "line", "lines", "test", "tests",
}
# Short words and keywords that are not evidence of naming the cited code.
_SHORT_STOP_WORDS = {
    "if", "in", "is", "or", "as", "do", "of", "to", "on", "by", "at", "it", "be", "an", "we", "my",
    "no", "up", "so", "the", "and", "for", "not", "def", "let", "var", "int", "str", "new", "try",
    "fn", "pub", "use", "mod", "end", "nil", "has", "are", "was", "can", "you", "how", "why", "who",
    "all", "any", "one", "two", "its", "but", "out", "get", "set",
}


class InterviewGenerationError(RuntimeError):
    """No valid interview set could be produced; the message is safe to show to a recruiter."""


# --- Selection (deterministic) -------------------------------------------------------------------

def _skill_name(skill_id: str) -> str:
    return (skill_map().get(skill_id) or {}).get("name", skill_id)


def _warning_text(signal: dict[str, Any]) -> str:
    """The evidence packet's wording for a warning (a signal's label names the check, not the problem)."""
    if signal["id"] in ATTRIBUTION_WARNINGS:
        return ATTRIBUTION_WARNINGS[signal["id"]].removeprefix("Advisory warning: ")
    return _cap_reason(signal).removeprefix("Capped at Medium: ").rstrip(".")


def _warning_labels(item: dict[str, Any]) -> list[str]:
    """Advisory attribution warnings on one citation, each once (recruiter-only signals)."""
    watched = set(ATTRIBUTION_WARNINGS) | set(MEDIUM_CAPS)
    texts = [
        _warning_text(signal)
        for signal in item.get("integrity") or []
        if signal.get("id") in watched and signal.get("status") == "warn"
    ]
    return list(dict.fromkeys(texts))


def _line_share_ok(item: dict[str, Any]) -> bool:
    share = line_share(item)
    return share is not None and share[1] > 0 and share[0] * 2 >= share[1]


def _usable_code(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Code citations whose stored lines match their range (and their evidence record, if any)."""
    usable = []
    for item in evidence:
        if item.get("type") != "code" or not item.get("snippet"):
            continue
        start, end = item.get("start_line") or 0, item.get("end_line") or 0
        if start < 1 or end < start or len(item["snippet"].split("\n")) != end - start + 1:
            continue
        recorded = (item.get("record") or {}).get("snippet_sha256")
        if recorded and recorded != snippet_sha256(item["snippet"]):
            continue
        usable.append(item)
    return usable


def _pick_citation(code: list[dict[str, Any]], prefer_warned: bool) -> dict[str, Any]:
    """The warned citation (attribution review) or the candidate's own lines (deeper
    questions); then the longest range."""
    return min(code, key=lambda item: (
        (not _warning_labels(item)) if prefer_warned else bool(_warning_labels(item)),
        not _line_share_ok(item),
        -(item["end_line"] - item["start_line"]),
        item["repo"], item["file_path"], item["start_line"],
    ))


def _repo_url(evidence_url: str) -> str | None:
    match = re.match(r"^(https://github\.com/[^/]+/[^/]+)/", evidence_url or "")
    return match.group(1) if match else None


def _citation(item: dict[str, Any]) -> dict[str, Any]:
    lines = item["snippet"].split("\n")[:MAX_SNIPPET_LINES]
    return {
        "repo": item["repo"],
        "repo_url": _repo_url(item.get("url", "")),
        "file_path": item["file_path"],
        "start_line": item["start_line"],
        "end_line": item["end_line"],
        "snippet_start_line": item["start_line"],
        "snippet_end_line": item["start_line"] + len(lines) - 1,
        "commit_sha": item.get("commit_sha"),
        "url": item.get("url"),
        "snippet": "\n".join(lines),
        "snippet_sha256": snippet_sha256(item["snippet"]),
        "warnings": _warning_labels(item),
    }


def select_interview_targets(passport: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic, job-aware selection. Same passport in, same targets out.

    Tiers: (1) must-have requirement with code evidence that needs an attribution review;
    (2) must-have requirement below its minimum level, questioned on its code if there is any,
    otherwise as a recruiter-led discussion; (3) other attribution concerns; (4) strong verified
    evidence (Applied/Demonstrated) for deeper questions. Within a tier: must-haves, then
    nice-to-haves, then skills outside the role, in role-match order, then by skill id. Each
    skill appears once and no file is cited twice (a skill cited only in files already chosen
    is skipped). Never reads or changes a level.
    """
    requirements = (passport.get("role_match") or {}).get("requirements") or []
    req_index = {item["skill_id"]: index for index, item in enumerate(requirements)}
    req_by_skill = {item["skill_id"]: item for item in requirements}
    quotes = {
        item["skill_id"]: item.get("quote")
        for item in ((passport.get("custom_role") or {}).get("requirements") or [])
    }
    skills = {item["skill_id"]: item for item in passport.get("skills") or []}
    earlier_source_skills = {
        (item.get("claim") or {}).get("skill_id")
        for item in passport.get("rejected_claims") or []
        if item.get("kind") in EARLIER_SOURCE_KINDS
    }
    ordered_ids = [item["skill_id"] for item in requirements] + sorted(
        skill_id for skill_id in skills if skill_id not in req_by_skill
    )

    candidates = []
    for skill_id in ordered_ids:
        requirement = req_by_skill.get(skill_id)
        skill = skills.get(skill_id) or {}
        evidence = skill.get("evidence") or []
        level = skill.get("level")
        code = _usable_code(evidence)
        status = (requirement or {}).get("status") or ("met" if evidence else "missing")
        attribution, _ = attribution_details(
            status, evidence if status != "missing" else [], earlier_source=skill_id in earlier_source_skills,
        )
        must = (requirement or {}).get("importance") == "must"
        incomplete = status in {"partial", "missing"}
        concern = bool(code) and (
            attribution in CONCERN_LABELS
            or skill_id in earlier_source_skills
            or any(_warning_labels(item) for item in code)
        )
        reasons = [REASON_MUST if must else REASON_NICE] if requirement else []
        if code:
            if must and concern:
                tier = 1
            elif must and incomplete:
                tier = 2
            elif concern and (requirement or level in STRONG_LEVELS):
                tier = 3
            elif level in STRONG_LEVELS:
                tier = 4
            else:
                continue
            if concern:
                reasons.append(f"{REASON_ATTRIBUTION} (attribution {attribution})")
            if incomplete and requirement:
                reasons.append(
                    f"Evidence below the required level: requires {requirement.get('min_level')}, "
                    f"found {requirement.get('student_level') or 'none'}"
                )
            if tier == 4:
                reasons.append(REASON_STRONG)
            kind = "code"
        elif must and incomplete:
            tier, kind = 2, "discussion"
            reasons.append(REASON_NO_CODE)
        else:
            continue
        importance_rank = 0 if must else 1 if requirement else 2
        candidates.append({
            "sort": (tier, importance_rank, req_index.get(skill_id, len(requirements)), skill_id),
            "tier": tier,
            "kind": kind,
            "skill_id": skill_id,
            "skill_name": _skill_name(skill_id),
            "level": level or "Not found",
            "attribution": attribution,
            "concern": concern,
            "code": code,
            "evidence": evidence,
            "requirement": {
                "importance": requirement.get("importance"),
                "status": requirement.get("status"),
                "min_level": requirement.get("min_level"),
                "student_level": requirement.get("student_level"),
                "quote": quotes.get(skill_id),
            } if requirement else None,
            "reasons": reasons,
        })

    targets: list[dict[str, Any]] = []
    used_files: set[tuple[str, str]] = set()
    counts = {"code": 0, "discussion": 0}
    limits = {"code": MAX_CODE_TARGETS, "discussion": MAX_DISCUSSION_TARGETS}
    for candidate in sorted(candidates, key=lambda item: item["sort"]):
        if len(targets) >= MAX_TARGETS or counts[candidate["kind"]] >= limits[candidate["kind"]]:
            continue
        if candidate["kind"] == "code":
            # Files are not reused: a skill cited only in files already chosen is skipped.
            candidate["code"] = [
                item for item in candidate["code"] if (item["repo"], item["file_path"]) not in used_files
            ]
            if not candidate["code"]:
                continue
        counts[candidate["kind"]] += 1
        target = {
            key: candidate[key]
            for key in ("tier", "kind", "skill_id", "skill_name", "level", "attribution", "requirement", "reasons")
        }
        target["target_id"] = f"T{len(targets) + 1}"
        if candidate["kind"] == "code":
            chosen = _pick_citation(candidate["code"], candidate["concern"])
            used_files.add((chosen["repo"], chosen["file_path"]))
            target["citation"] = _citation(chosen)
        else:
            target["citation"] = None
            related = next((item for item in candidate["evidence"] if item.get("url")), None)
            target["related_url"] = related.get("url") if related else None
        targets.append(target)
    return targets


# --- Generation ----------------------------------------------------------------------------------

class CodeTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=20, max_length=600)
    expected_behavior: str = Field(min_length=10, max_length=400)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=5)
    suggested_tests: list[str] = Field(min_length=1, max_length=4)
    evaluation_guidance: str = Field(min_length=10, max_length=400)


class ProposedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_id: str = Field(max_length=10)
    skill_id: str = Field(max_length=60)
    file_path: str = Field(max_length=400)
    question_type: Literal["explain", "design_tradeoff", "failure_case", "modification"]
    focus_start_line: int = Field(ge=1)
    focus_end_line: int = Field(ge=1)
    question: str = Field(min_length=15, max_length=500)
    follow_up: str | None = Field(default=None, max_length=300)
    answer_points: list[str] = Field(min_length=1, max_length=5)
    task: CodeTask | None = None


def _numbered(citation: dict[str, Any]) -> str:
    return "\n".join(
        f"{citation['snippet_start_line'] + offset}| {line}"
        for offset, line in enumerate(citation["snippet"].split("\n"))
    )


def build_prompt(targets: list[dict[str, Any]], role_name: str, previous: list[str]) -> str:
    anchors = [
        {
            "anchor_id": target["target_id"],
            "skill_id": target["skill_id"],
            "skill_name": target["skill_name"],
            "job_requirement": target["requirement"],
            "probe_authorship": "Attribution" in " ".join(target["reasons"]),
            "repo": target["citation"]["repo"],
            "file_path": target["citation"]["file_path"],
            "lines": f"{target['citation']['snippet_start_line']}-{target['citation']['snippet_end_line']}",
            "code": _numbered(target["citation"]),
        }
        for target in targets
    ]
    return (
        "You write interview questions for a recruiter who is checking whether a candidate can "
        "reason about code from their own repositories. Return exactly JSON: {\"questions\":[{"
        "\"anchor_id\",\"skill_id\",\"file_path\",\"question_type\",\"focus_start_line\","
        "\"focus_end_line\",\"question\",\"follow_up\",\"answer_points\",\"task\"}]}.\n"
        "Rules:\n"
        "- Use only the anchors below. Copy anchor_id, skill_id and file_path exactly from the anchor.\n"
        "- focus_start_line/focus_end_line must lie inside the anchor's numbered lines.\n"
        f"- 1 or 2 questions per anchor, at most {MAX_QUESTIONS_PER_ANCHOR}.\n"
        "- question_type: explain (purpose and flow of this code), design_tradeoff (a design choice "
        "or trade-off in it), failure_case (an edge case, bad input or failure mode), modification "
        "(a safe change to it and how they would test it). Across the set include at least one "
        "explain, one design_tradeoff or failure_case, and one modification.\n"
        "- Every question must name a function, variable, class or other identifier that appears "
        "in the focus lines. No language trivia or anything answerable without this repository.\n"
        "- Tie questions to the job requirement when one is given.\n"
        "- When probe_authorship is true, prefer questions the author could answer quickly but a "
        "reader could not: why this approach, what was tried first, what breaks if a line changes.\n"
        "- follow_up: one question that probes a vague answer, about a specific line or concrete "
        "scenario; or null.\n"
        "- answer_points: 2-4 short ideas a good answer covers; guidance, not an exact answer key.\n"
        "- task: null, except for at most ONE modification question in the whole set where the "
        "code supports a five-minute change; then {statement, expected_behavior, "
        "acceptance_criteria:[...], suggested_tests:[...], evaluation_guidance}.\n"
        "- Never invent files, line numbers or code. Never judge the candidate, levels or skills.\n"
        f"Target role: {role_name}\n"
        + (f"Avoid repeating these earlier questions: {json.dumps(previous, ensure_ascii=False)}\n" if previous else "")
        + f"Anchors: {json.dumps(anchors, ensure_ascii=False)}"
    )


def _identifiers(text: str) -> set[str]:
    return {
        word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", text)
        if word.lower() not in _STOP_WORDS
    }


def _short_identifiers(text: str) -> set[str]:
    """2-3 character names (single letters never count)."""
    return {
        word for word in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{1,2}\b", text)
        if word.lower() not in _SHORT_STOP_WORDS
    }


def _refers_to_code(question: ProposedQuestion, citation: dict[str, Any]) -> bool:
    """The main question names an identifier from its focus lines. A file name, a line number or
    a follow-up alone is not enough: any question could mention those.

    A short name (`add`, `id`) counts when the question writes it as code: in backticks, as a
    call `add(` or as an attribute `.add`. When the focus lines have no longer names at all, a
    short name also counts as a plain word."""
    lines = citation["snippet"].split("\n")
    first = question.focus_start_line - citation["snippet_start_line"]
    focus = "\n".join(lines[first:question.focus_end_line - citation["snippet_start_line"] + 1])
    text = question.question
    long_names = _identifiers(focus)
    if long_names & _identifiers(text):
        return True
    for name in _short_identifiers(focus):
        escaped = re.escape(name)
        if re.search(rf"`{escaped}`|\b{escaped}\s*\(|\.{escaped}\b", text):
            return True
        if not long_names and re.search(rf"\b{escaped}\b", text):
            return True
    return False


def validate_question(
    question: ProposedQuestion, anchors: dict[str, dict[str, Any]]
) -> str | None:
    """None when the question is grounded in a supplied anchor; otherwise the reason it isn't."""
    target = anchors.get(question.anchor_id)
    if target is None:
        return "Refers to evidence that was not selected for this set."
    if question.skill_id != target["skill_id"]:
        return f"Skill {question.skill_id!r} does not match the selected evidence."
    citation = target["citation"]
    if question.file_path != citation["file_path"]:
        return "Cites a file that is not the selected evidence."
    if not (
        citation["snippet_start_line"] <= question.focus_start_line
        <= question.focus_end_line <= citation["snippet_end_line"]
    ):
        return "Line range is outside the cited lines."
    if not _refers_to_code(question, citation):
        return "Does not refer to the cited code."
    if question.task and question.question_type != "modification":
        return "A code-change task must belong to a modification question."
    return None


def _parse(text: str) -> list[Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    data = json.loads(cleaned)
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ValueError('Expected a JSON object with a "questions" array.')
    return data["questions"]


def _validated(text: str, anchors: dict[str, dict[str, Any]]) -> tuple[list[ProposedQuestion], list[dict[str, Any]]]:
    """Valid questions in order and the dropped ones with reasons (no model text is kept)."""
    kept: list[ProposedQuestion] = []
    dropped: list[dict[str, Any]] = []
    per_anchor: dict[str, int] = {}
    seen_text: set[str] = set()
    task_used = False
    for index, raw in enumerate(_parse(text)):
        try:
            question = ProposedQuestion.model_validate(raw)
        except ValidationError as exc:
            problem = exc.errors(include_input=False, include_url=False)[0]
            location = ".".join(str(part) for part in problem["loc"]) or "item"
            dropped.append({"index": index, "reason": f"Invalid field {location}: {problem['msg']}"})
            continue
        reason = validate_question(question, anchors)
        normalized = re.sub(r"\W+", " ", question.question.lower()).strip()
        if reason is None and per_anchor.get(question.anchor_id, 0) >= MAX_QUESTIONS_PER_ANCHOR:
            reason = "More questions than allowed for one citation."
        if reason is None and normalized in seen_text:
            reason = "Duplicate question."
        if reason:
            dropped.append({"index": index, "skill_id": question.skill_id, "reason": reason})
            continue
        if question.task and task_used:
            question = question.model_copy(update={"task": None})  # one code-change task per set
        task_used = task_used or question.task is not None
        per_anchor[question.anchor_id] = per_anchor.get(question.anchor_id, 0) + 1
        seen_text.add(normalized)
        kept.append(question)
    return kept, dropped


def _balanced(questions: list[ProposedQuestion]) -> bool:
    types = {item.question_type for item in questions}
    if len(questions) < 3:
        return True
    return "explain" in types and bool(types & {"design_tradeoff", "failure_case"}) and "modification" in types


def generate_questions(
    targets: list[dict[str, Any]], role_name: str, previous: list[str]
) -> tuple[list[ProposedQuestion], list[dict[str, Any]]]:
    """One Gemini call (plus one retry on invalid output). Raises InterviewGenerationError."""
    if not config.GEMINI_API_KEY:
        raise InterviewGenerationError("Interview generation needs Gemini, which is not configured on this server.")
    anchors = {target["target_id"]: target for target in targets}
    prompt = build_prompt(targets, role_name, previous)
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    contents = prompt
    fallback: tuple[list[ProposedQuestion], list[dict[str, Any]]] | None = None
    for attempt in range(2):
        try:
            text = generate_json(client, contents)
        except Exception as exc:  # timeouts, rate limits and outages after generate_json's retries
            raise InterviewGenerationError(
                f"The question generator is unavailable right now ({exc.__class__.__name__}). Try again shortly."
            ) from exc
        try:
            questions, dropped = _validated(text, anchors)
        except (ValueError, json.JSONDecodeError):
            questions, dropped = [], [{"reason": "The response was not valid JSON."}]
        if questions and (_balanced(questions) or attempt == 1):
            return questions, dropped
        if questions and fallback is None:
            fallback = (questions, dropped)
        problems = "; ".join(sorted({item["reason"] for item in dropped})[:4]) or (
            "the set needs an explain, a design_tradeoff or failure_case, and a modification question"
        )
        contents = (
            f"{prompt}\nYour previous output was not usable: {problems}. "
            "Return corrected JSON only, using only the supplied anchors."
        )
    if fallback:
        return fallback
    raise InterviewGenerationError(
        "The generated questions did not pass validation against the cited code. Try again."
    )


# --- Session records -----------------------------------------------------------------------------

def _focus_url(citation: dict[str, Any], start: int, end: int) -> str | None:
    url = citation.get("url") or ""
    return re.sub(r"#L\d+(-L\d+)?$", f"#L{start}-L{end}", url) if url else None


def _code_question(question: ProposedQuestion, target: dict[str, Any]) -> dict[str, Any]:
    citation = target["citation"]
    return {
        "id": str(uuid.uuid4()),
        "skill_id": target["skill_id"],
        "skill_name": target["skill_name"],
        "requirement": target["requirement"],
        "selection_reasons": target["reasons"],
        "question_type": question.question_type,
        "question": question.question,
        "follow_up": question.follow_up,
        "answer_points": question.answer_points,
        "task": question.task.model_dump() if question.task else None,
        "anchor": {
            **{key: value for key, value in citation.items() if key != "warnings"},
            "focus_start_line": question.focus_start_line,
            "focus_end_line": question.focus_end_line,
            "focus_url": _focus_url(citation, question.focus_start_line, question.focus_end_line),
        },
        "attribution_warnings": citation["warnings"],
        "source": "generated",
    }


def _discussion_question(target: dict[str, Any]) -> dict[str, Any]:
    name = target["skill_name"]
    if target["level"] == "Used":
        question = f"Where do you use {name} beyond listing it in a dependency or configuration file? Walk me through that code."
    else:
        question = f"Walk me through a project where you used {name}. What did you build, and which decisions were yours?"
    return {
        "id": str(uuid.uuid4()),
        "skill_id": target["skill_id"],
        "skill_name": name,
        "requirement": target["requirement"],
        "selection_reasons": target["reasons"],
        "question_type": "discussion",
        "question": question,
        "follow_up": "Which part of that work could you show me afterwards, and where does it live?",
        "answer_points": DISCUSSION_GUIDANCE,
        "task": None,
        "anchor": None,
        "related_url": target.get("related_url"),
        "attribution_warnings": [],
        "source": "template",
    }


def source_revision(passport: dict[str, Any]) -> str | None:
    return passport.get("updated_at") or passport.get("created_at")


def build_interview_set(passport: dict[str, Any], previous_questions: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(session payload, question records). Raises InterviewGenerationError; never returns a
    set with a question whose citation failed validation."""
    targets = select_interview_targets(passport)
    if not targets:
        raise InterviewGenerationError(
            "No requirement or verified code evidence to build interview questions from."
        )
    code_targets = [target for target in targets if target["kind"] == "code"]
    role_name = (passport.get("role_match") or {}).get("role_name") or passport.get("target_role") or ""
    questions: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    if code_targets:
        generated, dropped = generate_questions(
            code_targets, role_name, previous_questions[:MAX_PREVIOUS_QUESTIONS]
        )
        by_id = {target["target_id"]: target for target in code_targets}
        # Keep the selection order (highest priority first), then the model's order per anchor.
        for target in code_targets:
            questions += [_code_question(item, by_id[item.anchor_id]) for item in generated if item.anchor_id == target["target_id"]]
    questions += [_discussion_question(target) for target in targets if target["kind"] == "discussion"]
    payload = {
        "prompt_version": PROMPT_VERSION,
        "model": config.GEMINI_MODEL if code_targets else None,
        "role_name": role_name,
        "targets": [
            {key: target.get(key) for key in ("target_id", "kind", "skill_id", "skill_name", "level", "attribution", "requirement", "reasons")}
            | {"citation": (
                {key: target["citation"][key] for key in ("repo", "file_path", "start_line", "end_line", "url")}
                if target["citation"] else None
            )}
            for target in targets
        ],
        "dropped": dropped,
    }
    return payload, questions


def _current_code_citations(passport: dict[str, Any] | None) -> set[tuple[Any, ...]]:
    if not passport:
        return set()
    return {
        (item.get("repo"), item.get("file_path"), item.get("start_line"), item.get("end_line"),
         item.get("commit_sha"), snippet_sha256(item.get("snippet") or ""))
        for skill in passport.get("skills") or []
        for item in skill.get("evidence") or []
        if item.get("type") == "code"
    }


STALE_REASON = (
    "This citation is no longer in the passport's current evidence (the passport was re-checked "
    "after this set was generated). Generate a new set to ask about the current code."
)


def session_summary(questions: list[dict[str, Any]], assessments: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Counts from saved outcomes only; an unassessed question never counts as explained."""
    counts = {outcome: 0 for outcome in OUTCOMES}
    for question in questions:
        outcome = (assessments.get(question["id"]) or {}).get("outcome")
        if outcome in counts:
            counts[outcome] += 1
    total = len(questions)
    assessed = sum(counts.values())
    text = f"Interview check: {counts['explained_well']} of {total} question{'' if total == 1 else 's'} explained well"
    if assessed < total:
        text += f" · {total - assessed} not yet assessed"
    return {"total": total, "assessed": assessed, "unassessed": total - assessed, **counts, "text": text}


def session_view(session: dict[str, Any], passport: dict[str, Any] | None) -> dict[str, Any]:
    """The recruiter-facing session: questions with their saved assessment and staleness."""
    current = _current_code_citations(passport)
    revision_changed = source_revision(passport or {}) != session.get("source_revision")
    questions = []
    for question in session.get("questions", []):
        anchor = question.get("anchor")
        stale = bool(anchor) and (
            passport is None
            or (revision_changed and (
                anchor["repo"], anchor["file_path"], anchor["start_line"], anchor["end_line"],
                anchor.get("commit_sha"), anchor.get("snippet_sha256"),
            ) not in current)
        )
        questions.append({
            **question,
            "type_label": QUESTION_TYPE_LABELS.get(question["question_type"], question["question_type"]),
            "stale": stale,
            "stale_reason": STALE_REASON if stale else None,
            "assessment": session.get("assessments", {}).get(question["id"]),
        })
    payload = session.get("payload") or {}
    return {
        "id": session["id"],
        "passport_id": session["passport_id"],
        "created_by": session["recruiter_id"],
        "status": session["status"],
        "created_at": session["created_at"],
        "updated_at": session.get("updated_at"),
        "source_revision": session.get("source_revision"),
        "passport_changed": revision_changed,
        "error": session.get("error"),
        "role_name": payload.get("role_name"),
        "targets": payload.get("targets", []),
        "dropped": payload.get("dropped", []),
        "questions": questions,
        "summary": session_summary(session.get("questions", []), session.get("assessments", {})),
        "outcome_labels": OUTCOMES,
    }
