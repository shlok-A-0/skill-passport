import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.resume_parse import match_resume_skills, parse_resume, redact_personal_data

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


@pytest.mark.parametrize(
    ("filename", "expected_skills"),
    [
        ("demo-backend.pdf", {"python", "fastapi", "docker", "aws"}),
        ("demo-frontend.pdf", {"javascript", "react", "kubernetes", "mysql"}),
        ("demo-ml.pdf", {"python", "pandas", "scikit-learn", "docker", "aws"}),
    ],
)
def test_demo_resumes_are_parseable_and_visibly_labelled(
    filename: str, expected_skills: set[str]
) -> None:
    result = parse_resume((SAMPLES / filename).read_bytes())
    skills = {item["skill_id"] for item in result["skills"]}
    assert expected_skills <= skills
    assert "DEMO DATA" in result["redacted_text"]
    assert "[REDACTED NAME]" in result["redacted_text"]
    assert "[REDACTED EMAIL]" in result["redacted_text"]
    assert "[REDACTED PHONE]" in result["redacted_text"]


def test_redaction_removes_identifiers_and_institution_lines() -> None:
    result = redact_personal_data(
        "DEMO DATA resume\nAlex Example\nalex@example.test\n"
        "555-010-2222\n10 Main Street\nExample University\n"
        "Age: 21\nSkills\nPython"
    )
    for private_value in (
        "Alex Example",
        "alex@example.test",
        "555-010-2222",
        "10 Main Street",
        "Example University",
        "Age: 21",
    ):
        assert private_value not in result
    assert "Python" in result


@pytest.mark.parametrize("separator", ["|", "•", "·", "—", "–", " - ", ",", "/", ";", ":"])
def test_redacted_text_hides_name_before_separated_header_text(separator: str) -> None:
    result = redact_personal_data(f"Jordan Smith {separator} Python {separator} React\nExperience")
    assert "Jordan Smith" not in result
    assert "[REDACTED NAME]" in result
    assert "Python" in result and "React" in result


def test_redacted_text_hides_name_with_label_or_parenthesized_title() -> None:
    labelled = redact_personal_data("Name: Jordan Smith\nPython")
    assert "Jordan Smith" not in labelled

    titled = redact_personal_data("Jordan Smith (Software Engineer)\nPython")
    assert "Jordan Smith" not in titled
    assert "[REDACTED NAME]" in titled


@pytest.mark.parametrize("label", ["Full Name", "Candidate Name"])
def test_redacted_text_handles_labeled_names_without_removing_skills(label: str) -> None:
    result = redact_personal_data(f"{label}: Jordan Smith | Python | React\nExperience")
    assert "Jordan Smith" not in result
    assert "[REDACTED NAME]" in result
    assert "Python" in result and "React" in result


def test_job_title_header_is_not_redacted_as_a_name() -> None:
    result = redact_personal_data("Software Engineer | Python | React\nExperience")
    assert "Software Engineer" in result
    assert "Python" in result and "React" in result


def test_redacted_text_checks_more_than_eight_header_lines_and_replaces_repeats() -> None:
    text = "Resume\n" + "\n".join(f"Contact detail {index}" for index in range(9))
    text += "\nJordan Smith | Python\nJordan Smith built APIs."
    result = redact_personal_data(text)
    assert "Jordan Smith" not in result
    assert result.count("[REDACTED NAME]") == 2


def test_resume_skill_mentions_do_not_retain_names_from_shared_header_lines() -> None:
    mentions = match_resume_skills("Jordan Smith | Python | React")
    relevant = [item for item in mentions if item["skill_id"] in {"python", "react"}]
    assert {item["skill_id"] for item in relevant} == {"python", "react"}
    assert {item["mention"] for item in relevant} == {"Python", "React"}
    assert all("Jordan Smith" not in item["mention"] for item in relevant)


def test_invalid_pdf_fails_with_clear_parse_error() -> None:
    with pytest.raises(ValueError, match="Could not parse resume PDF"):
        parse_resume(b"not a PDF document")
