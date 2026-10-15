import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.resume_parse import parse_resume, redact_personal_data

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


def test_invalid_pdf_fails_with_clear_parse_error() -> None:
    with pytest.raises(ValueError, match="Could not parse resume PDF"):
        parse_resume(b"not a PDF document")
