import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.growth_and_kit import parse_growth_and_kit
from app.services.role_match import match_role


def test_role_match_distinguishes_met_partial_and_claimed_missing() -> None:
    skills = [
        {
            "skill_id": "python",
            "level": "Applied",
            "evidence": [{"type": "code"}],
        },
        {
            "skill_id": "docker",
            "level": "Used",
            "evidence": [{"type": "config"}],
        },
        {"skill_id": "sql", "level": "Claimed", "evidence": []},
    ]
    role = {
        "id": "test-role",
        "name": "Test role",
        "requirements": [
            {"skill_id": "docker", "importance": "must", "min_level": "Applied"},
            {"skill_id": "python", "importance": "must", "min_level": "Applied"},
            {"skill_id": "sql", "importance": "nice", "min_level": "Used"},
        ],
    }
    result = match_role(skills, role)
    assert result["met"] == 1
    assert result["total"] == 3
    assert [item["status"] for item in result["requirements"]] == [
        "partial", "met", "missing",
    ]
    assert result["requirements"][0]["reason"].startswith("Requires Applied; found Used")
    assert result["requirements"][2]["student_level"] == "Claimed"


def test_unknown_role_is_an_explicit_error() -> None:
    with pytest.raises(ValueError, match="Unknown target role"):
        match_role([], "not-a-role")


def test_growth_response_schema_requires_two_interview_questions() -> None:
    valid = (
        '{"growth_tasks":[{"skill_id":"docker","target_repo":"demo",'
        '"task":"Add a Dockerfile and build the app in a clean image.",'
        '"evidence_it_will_create":"The Dockerfile will show a reproducible container build."}],'
        '"interview_kit":[{"skill_id":"flask","questions":['
        '{"q":"Explain the Flask route at app.py lines 1-4.","good_answer_mentions":"Route handler and response shape."},'
        '{"q":"How would you validate input in app.py lines 1-4?","good_answer_mentions":"Request validation and error handling."}]}]}'
    )
    result = parse_growth_and_kit(valid)
    assert len(result.interview_kit[0].questions) == 2
    with pytest.raises(ValueError):
        parse_growth_and_kit(valid.replace(
            '{"q":"How would you validate input in app.py lines 1-4?","good_answer_mentions":"Request validation and error handling."}',
            "",
        ))
