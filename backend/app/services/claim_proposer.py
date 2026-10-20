import hashlib
import json
import re
from typing import Any

from google import genai
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import CACHE_DIR, GEMINI_API_KEY, GEMINI_MODEL
from app.services.gemini import generate_json
from app.services.framework import get_skills


class ProposedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    repo: str
    file_path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    explanation: str = Field(min_length=1, max_length=500)


class ClaimProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ProposedClaim]


def parse_proposal(text: str) -> ClaimProposal:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    proposal = ClaimProposal.model_validate_json(cleaned)
    allowed_ids = {skill["id"] for skill in get_skills()}
    for claim in proposal.claims:
        if claim.skill_id not in allowed_ids:
            raise ValueError(f"Unknown skill id in proposal: {claim.skill_id}")
    return proposal


def _lenient_proposal(text: str) -> ClaimProposal:
    """Schema errors still fail, but an unknown skill id or a 4th citation is dropped, not fatal.

    Dropped claims never reach the verifier, so nothing unverified can slip through.
    """
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    proposal = ClaimProposal.model_validate_json(cleaned)
    allowed_ids = {skill["id"] for skill in get_skills()}
    counts: dict[str, int] = {}
    kept = []
    for claim in proposal.claims:
        if claim.skill_id not in allowed_ids:
            continue
        counts[claim.skill_id] = counts.get(claim.skill_id, 0) + 1
        if counts[claim.skill_id] <= 3:
            kept.append(claim)
    return ClaimProposal(claims=kept)


MAX_PROMPT_LINES = 8000


def _files_for_prompt(
    ingestion: dict[str, Any], max_lines: int | None = None
) -> list[tuple[str, str, dict[str, Any]]]:
    """Source files within the line budget, dropping the lowest-priority files first.

    Priority: each repo's first-selected file across all repos, then each repo's second, and so on
    (ingestion orders files by the user's recent changes; repos by most recent push).
    """
    ranked = sorted(
        (
            (file_index, repo_index, repo["name"], path, source)
            for repo_index, repo in enumerate(ingestion["repos"])
            for file_index, (path, source) in enumerate(repo["source_files"].items())
        ),
        key=lambda item: item[:2],
    )
    max_lines = MAX_PROMPT_LINES if max_lines is None else max_lines
    kept: set[tuple[int, int]] = set()
    total = 0
    for file_index, repo_index, _, _, source in ranked:
        size = len(source["lines"])
        if total + size > max_lines:
            break
        total += size
        kept.add((file_index, repo_index))
    # Keep each repo's files together in the prompt.
    return [
        (name, path, source)
        for file_index, repo_index, name, path, source in sorted(ranked, key=lambda item: (item[1], item[0]))
        if (file_index, repo_index) in kept
    ]


def _build_prompt(ingestion: dict[str, Any], resume_skills: list[dict[str, str]]) -> str:
    skills = [{"id": skill["id"], "name": skill["name"]} for skill in get_skills()]
    files = [
        {
            "repo": repo_name,
            "file_path": path,
            "lines": [
                {"line": number, "text": line}
                for number, line in enumerate(source["lines"], start=1)
            ],
        }
        for repo_name, path, source in _files_for_prompt(ingestion)
    ]
    resume_ids = sorted({item["skill_id"] for item in resume_skills})
    return (
        "Propose only skills directly supported by the supplied source-code lines. "
        "Return JSON with a claims array. Each claim must contain skill_id, repo, "
        "file_path, start_line, end_line, and explanation. Use only supplied skill ids "
        "and exact repository/file names and line numbers. Cite the most specific range, "
        "no more than three claims for any one skill, and never invent evidence. "
        "Resume skill ids are context only and are not proof. You do not decide verification.\n"
        f"Allowed skills: {json.dumps(skills)}\n"
        f"Resume-mentioned skill ids: {json.dumps(resume_ids)}\n"
        f"Source files: {json.dumps(files, ensure_ascii=False)}"
    )


def propose_claims(
    ingestion: dict[str, Any], resume_skills: list[dict[str, str]]
) -> list[dict[str, Any]]:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured; claim proposals require Gemini.")
    prompt = _build_prompt(ingestion, resume_skills)
    cache_key = hashlib.sha256(
        f"{GEMINI_MODEL}\0{prompt}".encode("utf-8")
    ).hexdigest()
    cache_path = CACHE_DIR / f"claims-{cache_key}.json"
    if cache_path.exists():
        try:
            return ClaimProposal.model_validate_json(
                cache_path.read_text(encoding="utf-8")
            ).model_dump(mode="json")["claims"]
        except (UnicodeDecodeError, ValueError, ValidationError):
            cache_path.unlink(missing_ok=True)

    client = genai.Client(api_key=GEMINI_API_KEY)
    contents = prompt
    last_error: Exception | None = None
    for attempt in range(2):
        response_text = generate_json(client, contents)
        try:
            proposal = _lenient_proposal(response_text)
            payload = proposal.model_dump(mode="json")
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
            return payload["claims"]
        except (ValueError, ValidationError) as exc:
            last_error = exc
            if attempt == 0:
                contents = (
                    f"{prompt}\nYour previous output was invalid: {exc}. "
                    "Return corrected JSON only, preserving the schema and citation rules."
                )
    raise RuntimeError(f"Gemini returned invalid claim proposals after one retry: {last_error}")
