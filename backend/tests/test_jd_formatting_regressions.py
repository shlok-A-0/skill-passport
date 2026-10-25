"""Regression tests for job-description parsing (2026-10-25 investigation and fixes).

The first six tests pin bugs that were confirmed through POST /roles/from-description
in exact_only mode and are now fixed: case-insensitive everyday-word skills in tech
contexts, sentence-start "React is required.", numbered requirement lists, and
"We require X" / inline "Requirements: X, Y" must-have phrasing. They fail if any
of those bugs regress. No API keys are required: the AI path is monkeypatched out.

The last two tests DOCUMENT current behaviour that is a product/policy decision
(skill-framework coverage, empty interview kit), not an accident: changing those
behaviours needs an explicit decision, and these tests pin the current contract.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import jd_parse
from app.services.gemini import GeminiUnavailableError
from app.services.jd_parse import JobDescriptionError
from app.services.growth_and_kit import parse_growth_and_kit


def _exact_only(monkeypatch, title: str, text: str) -> dict:
    """Force the exact-matching fallback path (no AI suggestions)."""
    def _fail(prompt, key, cache_prefix):
        raise GeminiUnavailableError("AI unavailable in test")

    monkeypatch.setattr(jd_parse, "propose_json_items", _fail)
    return jd_parse.extract_requirements(title, text)


def _ids(result: dict) -> set[str]:
    return {item["skill_id"] for item in result["requirements"]}


# --- Fixed bug 1: everyday-word skills match case-insensitively in tech contexts -------
# "react"/"express" (lowercase) match in unambiguous tech contexts; ordinary English
# ("react to feedback") still does not match.

def test_lowercase_react_is_found(monkeypatch) -> None:
    result = _exact_only(monkeypatch, "Frontend Intern", "We need strong react experience for our dashboard.")
    assert "react" in _ids(result)


def test_lowercase_express_is_found(monkeypatch) -> None:
    result = _exact_only(monkeypatch, "Backend Intern", "We are hiring. Must have express and Python experience.")
    assert {"express", "python"} <= _ids(result)


# --- Fixed bug 2: sentence-start "React is required." matches ------------------------

def test_bullet_lowercase_react_is_a_must_have(monkeypatch) -> None:
    text = "Requirements:\n- react is a must-have.\n- familiarity with docker is a plus."
    result = _exact_only(monkeypatch, "Frontend Intern", text)
    by_id = {item["skill_id"]: item["importance"] for item in result["requirements"]}
    assert by_id.get("react") == "must" and by_id.get("docker") == "nice"


def test_all_caps_react_is_a_must_have(monkeypatch) -> None:
    text = "REQUIREMENTS:\n- REACT IS A MUST-HAVE.\n- FAMILIARITY WITH DOCKER IS A PLUS."
    result = _exact_only(monkeypatch, "Frontend Intern", text)
    by_id = {item["skill_id"]: item["importance"] for item in result["requirements"]}
    assert by_id.get("react") == "must" and by_id.get("docker") == "nice"


def test_ordinary_english_common_words_do_not_match(monkeypatch) -> None:
    """False-positive protection: everyday uses of react/express/go/shell are not skills."""
    with pytest.raises(JobDescriptionError, match="No skills from the skill framework"):
        _exact_only(
            monkeypatch,
            "Team Lead",
            "We want someone who can react to feedback, express ideas clearly and "
            "is ready to go the extra mile every single day.",
        )


def test_sentence_start_react_is_found(monkeypatch) -> None:
    result = _exact_only(monkeypatch, "Frontend Intern", "React is required for this role. Python is a plus.")
    assert "react" in _ids(result)


# --- Fixed bug 3: numbered-list items keep the current section ------------------------
# "1. Python" under "Requirements:" is a must-have, not a section reset to nice.

def test_numbered_list_skills_are_found(monkeypatch) -> None:
    text = "Backend Intern\nRequirements:\n1. Python\n2. FastAPI\n3. SQL"
    result = _exact_only(monkeypatch, "Backend Intern", text)
    assert {"python", "fastapi", "sql"} <= _ids(result)


# --- Fixed bug 4: "We require X" and inline "Requirements: X, Y" are must-haves -----

def test_we_require_phrasing_is_must_have(monkeypatch) -> None:
    result = _exact_only(monkeypatch, "Backend Intern", "We are hiring for our platform team. We require Python and FastAPI for this role.")
    must = {item["skill_id"] for item in result["requirements"] if item["importance"] == "must"}
    assert {"python", "fastapi"} <= must


JD_INLINE_HEADING = "Backend Intern\nRequirements: Python, FastAPI and SQL."


def test_inline_requirements_heading_is_parsed(monkeypatch) -> None:
    result = _exact_only(monkeypatch, "Backend Intern", JD_INLINE_HEADING)
    must = {item["skill_id"] for item in result["requirements"] if item["importance"] == "must"}
    assert {"python", "fastapi", "sql"} <= must


# --- Documented current contract (product decisions, not accidents) -------------------

def test_jd_with_only_out_of_framework_skills_is_rejected(monkeypatch) -> None:
    """terraform/vue/nuxt/rust/webassembly are not in the 30-skill framework, so a JD
    naming only those is rejected with 'No skills from the skill framework'.
    Widening the framework is a product decision; this pins current behaviour."""
    with pytest.raises(JobDescriptionError, match="No skills from the skill framework"):
        _exact_only(monkeypatch, "Platform Engineer", "We are hiring a platform engineer. You must have Terraform experience with cloud infrastructure.")


def test_empty_interview_kit_validates_without_error() -> None:
    """GrowthAndKit.interview_kit has no minimum length, so a generation that returns
    growth tasks but zero interview entries succeeds silently (no generation_error),
    leaving student and recruiter views with no kit questions and no warning.
    Pins the current schema contract; whether an empty kit should raise/flag is an
    open product question."""
    parsed = parse_growth_and_kit(
        '{"growth_tasks": [{"skill_id": "python", "target_repo": "new-project",'
        ' "task": "Build a service", "evidence_it_will_create": "A working repository"}],'
        ' "interview_kit": []}'
    )
    assert parsed.interview_kit == []
    assert len(parsed.growth_tasks) == 1
