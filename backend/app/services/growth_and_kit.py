import hashlib
import json
import re
from typing import Any

from google import genai
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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

    @model_validator(mode="after")
    def unique_growth_skills(self) -> "GrowthAndKit":
        ids = [task.skill_id for task in self.growth_tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("Return exactly one growth task per skill.")
        return self


def parse_growth_and_kit(text: str) -> GrowthAndKit:
    cleaned = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    result = GrowthAndKit.model_validate_json(cleaned)
    allowed = set(skill_map())
    for task in result.growth_tasks:
        if task.skill_id not in allowed:
            raise ValueError(f"Unknown growth-task skill id: {task.skill_id}")
    for entry in result.interview_kit:
        if entry.skill_id not in allowed:
            raise ValueError(f"Unknown interview-kit skill id: {entry.skill_id}")
    return result


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
    verified = [
        item for item in skills
        if item["level"] in {"Applied", "Demonstrated"} and item.get("evidence")
    ]
    verified = verified[:5]
    interview_context = []
    for item in verified:
        code_evidence = next(
            (entry for entry in item["evidence"] if entry["type"] == "code"),
            None,
        )
        if code_evidence:
            interview_context.append(
                {
                    "skill_id": item["skill_id"],
                    "skill_name": skill_map()[item["skill_id"]]["name"],
                    "file": (
                        f"{code_evidence['repo']}/{code_evidence['file_path']}"
                        f"#L{code_evidence['start_line']}-L{code_evidence['end_line']}"
                    ),
                    "code": code_evidence["snippet"],
                }
            )
    prompt = (
        "Create concrete repository-specific growth tasks and an interview kit. "
        "Return exactly JSON: {growth_tasks:[{skill_id,target_repo,task,"
        "evidence_it_will_create}], interview_kit:[{skill_id,questions:[{q,"
        "good_answer_mentions},{q,good_answer_mentions}]}]}. Generate one growth "
        "task for every listed gap and no others. Use only allowed repository names "
        "as target_repo; if none exist, use \"new-project\". State what proof the "
        "task produces; never claim it is already achieved. Generate exactly two "
        "questions per supplied verified skill, based on its code, and reference "
        "the exact file and line range in each question. Do not assign levels, "
        "verification statuses, or role-match statuses.\n"
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
    cache_path = CACHE_DIR / f"growth-{cache_key}.json"
    if cache_path.exists():
        cached = parse_growth_and_kit(cache_path.read_text(encoding="utf-8"))
        return cached.model_dump(mode="json")

    repo_names = {repo["name"] for repo in ingestion.get("repos", [])}
    if not repo_names:
        repo_names = {"new-project"}
    client = genai.Client(api_key=GEMINI_API_KEY)
    contents = prompt
    last_error: Exception | None = None
    for attempt in range(2):
        response_text = generate_json(client, contents)
        try:
            result = parse_growth_and_kit(response_text)
            growth_ids = [task.skill_id for task in result.growth_tasks]
            interview_ids = [entry.skill_id for entry in result.interview_kit]
            if set(growth_ids) != set(expected_growth_ids):
                raise ValueError("Growth tasks do not match the exact role-gap skills.")
            if sorted(interview_ids) != sorted(expected_interview_ids):
                raise ValueError("Interview kit does not match the supplied verified skills.")
            result.interview_kit.sort(key=lambda entry: expected_interview_ids.index(entry.skill_id))
            if any(task.target_repo not in repo_names for task in result.growth_tasks):
                raise ValueError("A growth task used a repository not present in the user data.")
            payload = result.model_dump(mode="json")
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
            return payload
        except (ValueError, ValidationError) as exc:
            last_error = exc
            if attempt == 0:
                contents = (
                    f"{prompt}\nYour previous output was invalid: {exc}. "
                    "Return corrected JSON only and preserve all deterministic boundaries."
                )
    raise RuntimeError(f"Gemini returned invalid growth data after one retry: {last_error}")
