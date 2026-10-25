"""Regression tests for confirmed job-description parsing issues (2026-10-25 investigation).

Each xfail(strict=True) test asserts the DESIRED behaviour for a bug that is confirmed
present on this branch: it fails while the bug exists and turns into a hard CI failure
(XPASS) once the bug is fixed, forcing removal of the marker. Bugs were reproduced
through POST /roles/from-description in exact_only mode and through the extraction
function directly. No API keys are required: the AI path is monkeypatched out.

Non-xfail tests here DOCUMENT current behaviour that is a product/policy decision
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


# --- Confirmed bug 1: common-word skills only match one exact casing -----------------
# "react"/"express" (lowercase, as ordinary English words) are not matched at all, so a
# JD whose only framework skills are lowercase common words fails with "No skills found".

@pytest.mark.xfail(strict=True, reason="lowercase 'react' not matched (case-sensitive common-word gate)")
def test_lowercase_react_is_found(monkeypatch) -> None:
    result = _exact_only(monkeypatch, "Frontend Intern", "We need strong react experience for our dashboard.")
    assert "react" in _ids(result)


@pytest.mark.xfail(strict=True, reason="lowercase 'express' not matched (case-sensitive common-word gate)")
def test_lowercase_express_is_found(monkeypatch) -> None:
    result = _exact_only(monkeypatch, "Backend Intern", "Must have express and Python experience.")
    assert {"express", "python"} <= _ids(result)


# --- Confirmed bug 2: sentence-start "React is required." is missed ------------------
# "React" capitalised at the start of a sentence is ALSO not matched: a JD that says
# exactly that ends in the "No skills found" error even though it names a framework skill.

@pytest.mark.xfail(strict=True, reason="sentence-start 'React is required.' yields 'No skills found' error")
def test_sentence_start_react_is_found(monkeypatch) -> None:
    result = _exact_only(monkeypatch, "Frontend Intern", "React is required for this role. Python is a plus.")
    assert "react" in _ids(result)


# --- Confirmed bug 3: numbered-list lines lose their skills ---------------------------
# "1. Python\n2. FastAPI" style requirements lists are not parsed for those skills.

@pytest.mark.xfail(strict=True, reason="skills listed in a numbered list are not extracted")
def test_numbered_list_skills_are_found(monkeypatch) -> None:
    text = "Backend Intern\nRequirements:\n1. Python\n2. FastAPI\n3. SQL"
    result = _exact_only(monkeypatch, "Backend Intern", text)
    assert {"python", "fastapi", "sql"} <= _ids(result)


# --- Confirmed bug 4: "We require X" and inline "Requirements: X, Y" ---------------
# Neither phrasing is treated as a must-have signal, so such JDs fail with
# "No must-have requirement" even though they plainly state mandatory skills.

@pytest.mark.xfail(strict=True, reason="'We require X' is not treated as a must-have signal")
def test_we_require_phrasing_is_must_have(monkeypatch) -> None:
    result = _exact_only(monkeypatch, "Backend Intern", "We require Python and FastAPI for this role.")
    must = {item["skill_id"] for item in result["requirements"] if item["importance"] == "must"}
    assert {"python", "fastapi"} <= must


@pytest.mark.xfail(strict=True, reason="inline 'Requirements: X, Y' is not parsed for must-haves")
def test_inline_requirements_heading_is_parsed(monkeypatch) -> None:
    result = _exact_only(monkeypatch, "Backend Intern", "Requirements: Python, FastAPI and SQL.")
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
