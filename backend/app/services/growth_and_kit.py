import hashlib
import json
import re
from typing import Any

from google import genai
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import CACHE_DIR, GEMINI_API_KEY, GEMINI_MODEL
from app.services.gemini import generate_json
from app.services.framework import skill_map


class GrowthTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    target_repo: str
    task: str = Field(min_length=8, max_length=600)
    evidence_it_will_create: str = Field(min_length=8, max_length=400)


class InterviewQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str = Field(min_length=8, max_length=500)
    good_answer_mentions: str = Field(min_length=8, max_length=400)


class InterviewKitEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    questions: list[InterviewQuestion] = Field(min_length=2, max_length=2)


class GrowthAndKit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    growth_tasks: list[GrowthTask]
    interview_kit: list[InterviewKitEntry]


def parse_growth_and_kit(text: str) -> GrowthAndKit:
    """Schema validation only; unusable items are filtered by `_usable_items`."""
    cleaned = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    return GrowthAndKit.model_validate_json(cleaned)


def _usable_items(
    result: GrowthAndKit,
    expected_growth_ids: list[str],
    expected_interview_ids: list[str],
    repo_names: set[str],
) -> dict[str, Any]:
    """Keep tasks for real gaps on real repos and kit entries for the supplied skills; drop the rest."""
    tasks, seen = [], set()
    for task in result.growth_tasks:
        if task.skill_id in expected_growth_ids and task.target_repo in repo_names and task.skill_id not in seen:
            seen.add(task.skill_id)
            tasks.append(task)
    entries, seen = [], set()
    for entry in result.interview_kit:
        if entry.skill_id in expected_interview_ids and entry.skill_id not in seen:
            seen.add(entry.skill_id)
            entries.append(entry)
    entries.sort(key=lambda entry: expected_interview_ids.index(entry.skill_id))
    if expected_growth_ids and not tasks:
        raise ValueError("No usable growth task: each must target a listed gap and an allowed repository.")
    return GrowthAndKit(growth_tasks=tasks, interview_kit=entries).model_dump(mode="json")


def _describe_error(exc: Exception) -> str:
    """A short error text without the model's output (it may contain interview answers)."""
    if isinstance(exc, ValidationError):
        problems = [
            f"{'.'.join(str(part) for part in item['loc']) or 'response'}: {item['msg']}"
            for item in exc.errors(include_input=False, include_url=False)[:3]
        ]
        return "Invalid growth data: " + "; ".join(problems)
    return str(exc)[:300]


INTERVIEW_LEVELS = {"Demonstrated": 0, "Applied": 1}
MAX_INTERVIEW_SKILLS = 5


def _line_share_ok(evidence: dict[str, Any]) -> bool:
    return any(
        signal.get("id") == "line_share" and signal.get("status") == "ok"
        for signal in evidence.get("integrity") or []
    )


def _best_citation(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Code the student wrote most of (line_share ok) first, then the longest cited range."""
    code = [item for item in evidence if item.get("type") == "code"]
    return min(
        code,
        key=lambda item: (not _line_share_ok(item), -(item["end_line"] - item["start_line"])),
        default=None,
    )


def _interview_context(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The strongest verified skills (Demonstrated, then Applied, then most evidence), one citation each."""
    verified = sorted(
        (
            item for item in skills
            if item["level"] in INTERVIEW_LEVELS and _best_citation(item.get("evidence") or [])
        ),
        key=lambda item: (INTERVIEW_LEVELS[item["level"]], -len(item["evidence"])),
    )[:MAX_INTERVIEW_SKILLS]
    context = []
    for item in verified:
        citation = _best_citation(item["evidence"])
        context.append({
            "skill_id": item["skill_id"],
            "skill_name": skill_map()[item["skill_id"]]["name"],
            "file": (
                f"{citation['repo']}/{citation['file_path']}"
                f"#L{citation['start_line']}-L{citation['end_line']}"
            ),
            "code": citation.get("snippet", ""),
        })
    return context


def _build_prompt(
    skills: list[dict[str, Any]],
    role_match: dict[str, Any],
    ingestion: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    repos = [repo["name"] for repo in ingestion.get("repos", [])]
    repo_context = [
        {
            "name": repo["name"],
            "files": [
                {"path": path, "content": source["content"][:6000]}
                for path, source in list(repo.get("source_files", {}).items())[:4]
            ],
        }
        for repo in ingestion.get("repos", [])
    ]
    growth_gaps = [
        item for item in role_match["requirements"]
        if item["status"] in {"partial", "missing"}
    ]
    interview_context = _interview_context(skills)
    prompt = (
        "Create concrete repository-specific growth tasks and an interview kit. "
        "Return exactly JSON: {growth_tasks:[{skill_id,target_repo,task,"
        "evidence_it_will_create}], interview_kit:[{skill_id,questions:[{q,"
        "good_answer_mentions},{q,good_answer_mentions}]}]}. Generate one growth "
        "task for every listed gap and no others. Use only allowed repository names "
        "as target_repo; if none exist, use \"new-project\". State what proof the "
        "task produces; never claim it is already achieved. Generate exactly two "
        "questions per supplied verified skill, based on its code, and reference "
        "the exact file and line range in each question. Ask about design decisions in "
        "this code, how it fails (edge cases, errors, bad input), and how the candidate "
        "would modify this specific code for a new requirement. Do NOT ask generic "
        "language-trivia questions that could be answered without the repository. "
        "Do not assign levels, verification statuses, or role-match statuses.\n"
        f"Role: {role_match['role_name']}\n"
        f"Gaps: {json.dumps(growth_gaps)}\n"
        f"Allowed repositories: {json.dumps(repos or ['new-project'])}\n"
        f"Repository context: {json.dumps(repo_context, ensure_ascii=False)}\n"
        f"Verified skills and cited code: {json.dumps(interview_context, ensure_ascii=False)}"
    )
    return prompt, [item["skill_id"] for item in growth_gaps], [
        item["skill_id"] for item in interview_context
    ]


def generate_growth_and_kit(
    skills: list[dict[str, Any]],
    role_match: dict[str, Any],
    ingestion: dict[str, Any],
) -> dict[str, Any]:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured; growth and interview generation require Gemini.")
    prompt, expected_growth_ids, expected_interview_ids = _build_prompt(
        skills, role_match, ingestion
    )
    cache_key = hashlib.sha256(
        f"{GEMINI_MODEL}\0{prompt}".encode("utf-8")
    ).hexdigest()
    repo_names = {repo["name"] for repo in ingestion.get("repos", [])}
    if not repo_names:
        repo_names = {"new-project"}
    cache_path = CACHE_DIR / f"growth-{cache_key}.json"
    if cache_path.exists():
        try:
            cached = parse_growth_and_kit(cache_path.read_text(encoding="utf-8"))
            return _usable_items(cached, expected_growth_ids, expected_interview_ids, repo_names)
        except (ValueError, ValidationError):
            pass  # unusable cache entry: ask Gemini again

    client = genai.Client(api_key=GEMINI_API_KEY)
    contents = prompt
    last_error: Exception | None = None
    for attempt in range(2):
        response_text = generate_json(client, contents)
        try:
            payload = _usable_items(
                parse_growth_and_kit(response_text),
                expected_growth_ids, expected_interview_ids, repo_names,
            )
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
            return payload
        except (ValueError, ValidationError) as exc:
            last_error = exc
            if attempt == 0:
                contents = (
                    f"{prompt}\nYour previous output was invalid: {_describe_error(exc)}. "
                    "Return corrected JSON only and preserve all deterministic boundaries."
                )
    # The verified passport is still valid without the AI write-up.
    return {
        "growth_tasks": [],
        "interview_kit": [],
        "generation_error": _describe_error(last_error) if last_error else "Unknown error.",
    }
