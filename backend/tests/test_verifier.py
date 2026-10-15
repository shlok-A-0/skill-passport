import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.claim_proposer import parse_proposal
from app.services.verifier import verify_claims, verify_one_claim


def sample_ingestion(is_fork: bool = False) -> dict:
    lines = [
        "from flask import Flask",
        "app = Flask(__name__)",
        "",
        "@app.route('/health')",
        "def health():",
        "    return {'status': 'ok'}",
    ]
    return {
        "username": "student",
        "repos": [
            {
                "name": "demo-api",
                "full_name": "student/demo-api",
                "head_sha": "abc123",
                "is_fork": is_fork,
                "tree": ["app.py", "requirements.txt", "Dockerfile", "tests/test_app.py"],
                "source_files": {
                    "app.py": {
                        "content": "\n".join(lines),
                        "lines": lines,
                        "authored_shas": ["def456"],
                        "line_count": len(lines),
                    }
                },
                "dependency_files": ["requirements.txt"],
                "dependency_contents": {"requirements.txt": "flask==3.0.0\n"},
                "config_files": ["Dockerfile"],
                "config_contents": {"Dockerfile": "FROM python:3.11-slim\n"},
                "test_files": ["tests/test_app.py"],
            }
        ],
    }


def claim(**overrides: object) -> dict:
    result = {
        "skill_id": "flask",
        "repo": "demo-api",
        "file_path": "app.py",
        "start_line": 1,
        "end_line": 6,
        "explanation": "Defines a Flask health endpoint.",
    }
    result.update(overrides)
    return result


def test_valid_claim_passes_all_checks_and_cites_head_revision() -> None:
    result = verify_one_claim(claim(), sample_ingestion())
    assert result["verified"] is True
    assert all(result["checks"].values())
    assert result["evidence"]["url"] == (
        "https://github.com/student/demo-api/blob/abc123/app.py#L1-L6"
    )


def test_hallucinated_file_is_rejected() -> None:
    result = verify_one_claim(claim(file_path="missing.py"), sample_ingestion())
    assert result["verified"] is False
    assert "file_exists" in result["failed_checks"]


def test_out_of_range_lines_are_rejected() -> None:
    result = verify_one_claim(claim(end_line=99), sample_ingestion())
    assert result["verified"] is False
    assert "lines_valid" in result["failed_checks"]


def test_file_not_authored_by_user_is_rejected() -> None:
    ingestion = sample_ingestion()
    ingestion["repos"][0]["source_files"]["app.py"]["authored_shas"] = []
    result = verify_one_claim(claim(), ingestion)
    assert result["verified"] is False
    assert "authored" in result["failed_checks"]


def test_fork_repository_is_rejected() -> None:
    result = verify_one_claim(claim(), sample_ingestion(is_fork=True))
    assert result["verified"] is False
    assert "not_fork" in result["failed_checks"]


def test_resume_only_skill_remains_claimed_and_is_not_verified() -> None:
    result = verify_claims(
        [],
        sample_ingestion(),
        resume_skills=[{"skill_id": "aws", "mention": "AWS"}],
    )
    aws = next(skill for skill in result["skills"] if skill["skill_id"] == "aws")
    assert aws["level"] == "Claimed"
    assert aws["evidence"] == []


def test_dockerfile_only_skill_is_used_not_applied() -> None:
    result = verify_claims([], sample_ingestion())
    docker = next(skill for skill in result["skills"] if skill["skill_id"] == "docker")
    assert docker["level"] == "Used"
    assert docker["evidence"][0]["type"] == "config"


def test_dependency_evidence_cites_and_highlights_exact_package_line() -> None:
    ingestion = sample_ingestion()
    content = (
        "flask==3.0.0\n"
        "requests==2.32.0\n"
        "pandas==2.2.0\n"
        "numpy==1.26.0\n"
        "pytest==8.0.0\n"
    )
    repo = ingestion["repos"][0]
    repo["tree"].append("requirements.txt")
    repo["dependency_contents"] = {"requirements.txt": content}

    pandas = next(
        skill for skill in verify_claims([], ingestion)["skills"]
        if skill["skill_id"] == "pandas"
    )
    evidence = pandas["evidence"][0]
    assert evidence["start_line"] == evidence["end_line"] == 3
    assert evidence["url"].endswith("requirements.txt#L3-L3")
    assert evidence["snippet_start_line"] == 1
    assert evidence["matched_line"] == 3
    assert evidence["snippet"].splitlines()[2] == "pandas==2.2.0"


def test_package_json_dependency_evidence_cites_package_name_line() -> None:
    ingestion = sample_ingestion()
    content = (
        '{\n'
        '  "dependencies": {\n'
        '    "react": "^18.3.1",\n'
        '    "@aws-sdk/client-s3": "^3.0.0"\n'
        '  }\n'
        '}\n'
    )
    repo = ingestion["repos"][0]
    repo["tree"].append("package.json")
    repo["dependency_contents"] = {"package.json": content}

    aws = next(
        skill for skill in verify_claims([], ingestion)["skills"]
        if skill["skill_id"] == "aws"
    )
    evidence = aws["evidence"][0]
    assert evidence["start_line"] == evidence["end_line"] == 4
    assert evidence["url"].endswith("package.json#L4-L4")
    assert '"@aws-sdk/client-s3"' in evidence["snippet"]


def test_primary_dependency_files_are_listed_before_fixture_files() -> None:
    ingestion = sample_ingestion()
    repo = ingestion["repos"][0]
    repo["tree"].extend(["tests/fixtures/requirements.txt", "requirements.txt"])
    repo["dependency_contents"] = {
        "tests/fixtures/requirements.txt": "flask==3.0.0\n",
        "requirements.txt": "flask==3.1.0\n",
    }
    ingestion["repos"].append(
        {
            "name": "later-repo",
            "full_name": "student/later-repo",
            "head_sha": "def456",
            "is_fork": False,
            "tree": ["examples/requirements.txt"],
            "dependency_contents": {
                "examples/requirements.txt": "flask==2.0.0\n"
            },
        }
    )

    flask = next(
        skill for skill in verify_claims([], ingestion)["skills"]
        if skill["skill_id"] == "flask"
    )
    dependency_evidence = [
        item for item in flask["evidence"] if item["type"] == "dependency"
    ]
    assert [item["file_path"] for item in dependency_evidence] == [
        "requirements.txt",
        "examples/requirements.txt",
        "tests/fixtures/requirements.txt",
    ]


def test_verified_code_plus_same_repo_tests_is_demonstrated() -> None:
    result = verify_claims([claim()], sample_ingestion())
    flask = next(skill for skill in result["skills"] if skill["skill_id"] == "flask")
    assert flask["level"] == "Demonstrated"
    assert flask["evidence"][0]["checks"]["authored"] is True


def test_claim_proposal_schema_accepts_valid_json_and_rejects_unknown_skill() -> None:
    parsed = parse_proposal(
        '{"claims":[{"skill_id":"flask","repo":"demo-api","file_path":"app.py",'
        '"start_line":1,"end_line":6,"explanation":"Flask route"}]}'
    )
    assert parsed.claims[0].skill_id == "flask"
    with pytest.raises(ValueError, match="Unknown skill"):
        parse_proposal(
            '{"claims":[{"skill_id":"made-up","repo":"demo-api","file_path":"app.py",'
            '"start_line":1,"end_line":6,"explanation":"invented"}]}'
        )


def _signals(ingestion: dict) -> dict[str, dict]:
    result = verify_one_claim(claim(), ingestion)
    assert result["verified"] is True  # integrity signals never change verification
    return {item["id"]: item for item in result["evidence"]["integrity"]}


def test_integrity_signals_are_unknown_without_history_data() -> None:
    signals = _signals(sample_ingestion())
    signals.pop("ai_leftover")  # computed from the file text itself, so never unknown
    assert {item["status"] for item in signals.values()} == {"unknown"}


def test_integrity_signals_flag_a_bulk_uploaded_clone() -> None:
    ingestion = sample_ingestion()
    repo = ingestion["repos"][0]
    source = repo["source_files"]["app.py"]
    # Someone else wrote lines 1-4; the student touched only lines 5-6.
    source["blame"] = [[1, 4, "original-author"], [5, 6, "student"]]
    source["history"] = {
        "total": 1, "complete": True, "by_user": 0,
        "creator_login": "original-author", "creator_sha": "c0",
        "creation": {"files_changed": 120, "total_additions": 9000, "file_additions": 6},
    }
    repo["commit_counts"] = {"total": 1, "by_user": 1}
    signals = _signals(ingestion)
    assert signals["line_share"]["status"] == "warn"
    assert "2 of 6" in signals["line_share"]["detail"]
    assert signals["commit_share"]["status"] == "warn"
    assert "original-author" in signals["commit_share"]["detail"]
    assert signals["bulk_upload"]["status"] == "warn"
    assert signals["repo_history"]["status"] == "warn"


def test_integrity_signals_pass_for_incremental_own_work() -> None:
    ingestion = sample_ingestion()
    repo = ingestion["repos"][0]
    source = repo["source_files"]["app.py"]
    source["blame"] = [[1, 6, "Student"]]  # login match is case-insensitive
    source["history"] = {
        "total": 5, "complete": True, "by_user": 5,
        "creator_login": "student", "creator_sha": "c0",
        "creation": {"files_changed": 2, "total_additions": 30, "file_additions": 4},
    }
    repo["commit_counts"] = {"total": 24, "by_user": 24}
    signals = _signals(ingestion)
    assert {item["status"] for item in signals.values()} == {"ok"}


def test_copy_check_warns_only_when_the_other_repo_is_older(monkeypatch) -> None:
    from app.services import copy_check

    monkeypatch.setattr(
        copy_check, "_search",
        lambda client, line, username: {"total": 1, "matches": [{"repo": "x/y", "url": "u"}]},
    )
    evidence = {"snippet": "    return calculate_invoice_total(order_items, tax_rate)"}
    mine = "2024-06-01T00:00:00Z"
    monkeypatch.setattr(copy_check, "_repo_created_at", lambda client, name: "2023-01-01T00:00:00Z")
    signal = copy_check.copy_signal(evidence, object(), "student", mine)
    assert signal["status"] == "warn" and signal["matches"]
    # The other repo is newer: it may have copied from the student, so no warning.
    monkeypatch.setattr(copy_check, "_repo_created_at", lambda client, name: "2025-01-01T00:00:00Z")
    assert copy_check.copy_signal(evidence, object(), "student", mine)["status"] == "ok"
    # Without the student's repo date, the order can't be judged.
    assert copy_check.copy_signal(evidence, object(), "student")["status"] == "unknown"
    assert copy_check.copy_signal({"snippet": "import os"}, object(), "student")["status"] == "unknown"
    assert copy_check.copy_signal(evidence, None, "student")["status"] == "unknown"


def test_ai_chat_leftover_is_flagged_with_its_line() -> None:
    ingestion = sample_ingestion()
    source = ingestion["repos"][0]["source_files"]["app.py"]
    source["lines"] = source["lines"] + ["# Here's the updated code with error handling:"]
    signals = _signals(ingestion)
    assert signals["ai_leftover"]["status"] == "warn"
    assert "Line 7" in signals["ai_leftover"]["detail"]


def test_plain_code_has_no_ai_chat_leftover() -> None:
    assert _signals(sample_ingestion())["ai_leftover"]["status"] == "ok"


def _levels_with(**repo_overrides: object) -> dict[str, str]:
    ingestion = sample_ingestion()
    repo = ingestion["repos"][0]
    repo.update({"dependency_contents": {}, "test_files": [], "config_files": [], "config_contents": {}})
    repo.update(repo_overrides)
    return {item["skill_id"]: item["level"] for item in verify_claims([], ingestion)["skills"]}


def test_nested_dockerfile_counts_as_docker_evidence() -> None:
    levels = _levels_with(
        tree=["app.py", "backend/Dockerfile"],
        config_files=["backend/Dockerfile"],
        config_contents={"backend/Dockerfile": "FROM python:3.11\n"},
    )
    assert levels["docker"] == "Used"


def test_non_github_ci_counts_as_cicd_evidence() -> None:
    levels = _levels_with(
        tree=["app.py", ".travis.yml"],
        config_files=[".travis.yml"],
        config_contents={".travis.yml": "language: python\n"},
    )
    assert levels["cicd"] == "Used"


def test_authored_commits_count_as_git_evidence() -> None:
    ingestion = sample_ingestion()
    result = verify_claims([], ingestion)
    git = next(item for item in result["skills"] if item["skill_id"] == "git")
    assert git["level"] == "Used"
    assert git["evidence"][0]["type"] == "commit"
    assert git["evidence"][0]["url"] == "https://github.com/student/demo-api/commit/def456"


def test_git_is_not_evidenced_for_a_fork() -> None:
    assert "git" not in _levels_with(is_fork=True)


def test_authored_test_file_is_cited_as_code_and_test_files_are_grouped() -> None:
    ingestion = sample_ingestion()
    repo = ingestion["repos"][0]
    test_lines = ["from app import app", "", "def test_health():", "    assert app"]
    repo["tree"].append("tests/test_more.py")
    repo["test_files"] = ["tests/test_app.py", "tests/test_more.py"]
    repo["source_files"]["tests/test_app.py"] = {
        "content": "\n".join(test_lines), "lines": test_lines,
        "authored_shas": ["def456"], "line_count": len(test_lines),
    }
    repo["tree"].append("tests/test_app.py")
    skill = next(
        item for item in verify_claims([], ingestion)["skills"] if item["skill_id"] == "unit-testing"
    )
    code = [item for item in skill["evidence"] if item["type"] == "code"]
    config = [item for item in skill["evidence"] if item["type"] == "config"]
    assert code and code[0]["start_line"] == 3 and all(code[0]["checks"].values())
    assert len(config) == 1 and "2 test file(s)" in config[0]["explanation"]
    assert skill["level"] == "Demonstrated"  # tests exist in the same repo


def test_unauthored_test_file_is_not_cited() -> None:
    ingestion = sample_ingestion()
    repo = ingestion["repos"][0]
    lines = ["def test_health():", "    assert True"]
    repo["source_files"]["tests/test_app.py"] = {
        "content": "\n".join(lines), "lines": lines, "authored_shas": [], "line_count": 2,
    }
    skill = next(
        item for item in verify_claims([], ingestion)["skills"] if item["skill_id"] == "unit-testing"
    )
    assert skill["level"] == "Used"


def test_common_english_words_are_not_resume_skills() -> None:
    from app.services.resume_parse import match_resume_skills

    prose = "Ready to go the extra mile. I react calmly and express ideas. Passed the torch."
    assert match_resume_skills(prose) == []
    listed = {item["skill_id"] for item in match_resume_skills("Skills: Go, React, Express, PyTorch")}
    assert {"go", "react", "express", "pytorch"} <= listed


def test_signatures_match_whole_words_only() -> None:
    from app.services.verifier import contains_signature

    assert not contains_signature('socketio.emit("event")', "it(")
    assert contains_signature('  it("works", () => {', "it(")
    assert contains_signature('self.app.get("/")', "app.get(")
    assert contains_signature('query = "select * from users"', "SELECT ")


def test_docs_named_like_tests_are_not_test_files() -> None:
    from app.services.github_ingest import is_test_path

    assert not is_test_path("docs/api/test_client.rst")
    assert is_test_path("tests/test_app.py") and is_test_path("src/App.test.tsx")


def test_test_file_cited_by_ai_is_not_cited_twice() -> None:
    ingestion = sample_ingestion()
    repo = ingestion["repos"][0]
    lines = ["def test_health():", "    assert True"]
    repo["tree"].append("tests/test_app.py")
    repo["source_files"]["tests/test_app.py"] = {
        "content": "\n".join(lines), "lines": lines, "authored_shas": ["def456"], "line_count": 2,
    }
    ai_claim = claim(skill_id="unit-testing", file_path="tests/test_app.py", start_line=1, end_line=2)
    skill = next(
        item for item in verify_claims([ai_claim], ingestion)["skills"] if item["skill_id"] == "unit-testing"
    )
    assert len([item for item in skill["evidence"] if item["type"] == "code"]) == 1


def plain_python_ingestion() -> dict:
    lines = ["from os import path", "def add(a, b):", "    parts = 'a,b'.split(',')", "    return a + b"]
    data = sample_ingestion()
    data["repos"][0]["source_files"]["util.py"] = {
        "content": "\n".join(lines), "lines": lines, "authored_shas": ["def456"], "line_count": len(lines),
    }
    data["repos"][0]["tree"].append("util.py")
    return data


@pytest.mark.parametrize("skill_id", ["pytorch", "tensorflow", "aws", "rest-api", "docker", "unit-testing", "flask"])
def test_framework_claim_on_plain_code_is_rejected(skill_id: str) -> None:
    result = verify_one_claim(
        claim(skill_id=skill_id, file_path="util.py", start_line=1, end_line=4),
        plain_python_ingestion(),
    )
    assert result["verified"] is False
    assert "signature_found" in result["failed_checks"]


def test_language_claim_needs_matching_file_extension() -> None:
    data = plain_python_ingestion()
    assert verify_one_claim(claim(skill_id="python", file_path="util.py", start_line=1, end_line=4), data)["verified"]
    assert not verify_one_claim(claim(skill_id="javascript", file_path="util.py", start_line=1, end_line=4), data)["verified"]
