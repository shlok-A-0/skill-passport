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
                "authored_paths": {"requirements.txt": True, "Dockerfile": True},
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


def test_authored_dockerfile_makes_docker_used() -> None:
    docker = next(skill for skill in verify_claims([], sample_ingestion())["skills"] if skill["skill_id"] == "docker")
    assert docker["level"] == "Used"
    assert docker["evidence"][0]["checks"]["authored"] is True


def test_unauthored_dockerfile_does_not_make_docker_used() -> None:
    ingestion = sample_ingestion()
    ingestion["repos"][0]["authored_paths"] = {"requirements.txt": True, "Dockerfile": False}
    docker = next(skill for skill in verify_claims([], ingestion)["skills"] if skill["skill_id"] == "docker")
    assert docker["level"] == "Claimed"
    # The evidence stays visible, marked as not authored.
    assert docker["evidence"][0]["checks"]["authored"] is False
    from app.services.role_match import match_role

    docker_requirement = {"id": "r", "name": "R", "requirements": [
        {"skill_id": "docker", "importance": "must", "min_level": "Used"},
    ]}
    assert match_role(verify_claims([], ingestion)["skills"], docker_requirement)["met"] == 0


def test_missing_authorship_data_counts_as_not_authored() -> None:
    ingestion = sample_ingestion()
    del ingestion["repos"][0]["authored_paths"]
    levels = {item["skill_id"]: item["level"] for item in verify_claims([], ingestion)["skills"]}
    assert levels["docker"] == "Claimed" and levels["flask"] == "Claimed"
    # Authored code evidence is unaffected by dependency-file authorship.
    levels = {item["skill_id"]: item["level"] for item in verify_claims([claim()], ingestion)["skills"]}
    assert levels["flask"] == "Demonstrated"


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


def _commits(*days: str) -> list[dict]:
    return [
        {"sha": f"c{index}", "author_date": f"{day}T10:00:00Z", "committer_date": f"{day}T10:00:00Z"}
        for index, day in enumerate(days)
    ]


def _repo_signals(**repo_fields: object) -> dict[str, dict]:
    ingestion = sample_ingestion()
    ingestion["repos"][0].update(repo_fields)
    return _signals(ingestion)


def test_all_commits_on_one_day_in_a_large_repo_warns() -> None:
    signal = _repo_signals(
        user_commits=_commits("2024-03-05", "2024-03-05", "2024-03-05"),
        user_commits_complete=True, source_file_count=20,
    )["commit_spread"]
    assert signal["status"] == "warn"
    assert signal["detail"] == "All 3 of your commits in this repository were made on one day (2024-03-05)."


def test_all_commits_on_one_day_in_a_small_repo_is_ok() -> None:
    signal = _repo_signals(
        user_commits=_commits("2024-03-05", "2024-03-05"), user_commits_complete=True, source_file_count=5,
    )["commit_spread"]
    assert signal["status"] == "ok"


def test_commits_over_ten_days_are_ok() -> None:
    signal = _repo_signals(
        user_commits=_commits(*[f"2024-03-{day:02d}" for day in range(1, 11)]),
        user_commits_complete=True, source_file_count=40,
    )["commit_spread"]
    assert signal["status"] == "ok"
    assert signal["detail"] == "10 commits across 10 days, from 2024-03-01 to 2024-03-10."


def test_history_dated_before_repo_creation_warns() -> None:
    signal = _repo_signals(
        created_at="2024-04-01T00:00:00Z",
        user_commits=_commits("2024-03-02", "2024-03-02", "2024-03-02", "2024-03-02", "2024-04-02"),
    )["history_import"]
    assert signal["status"] == "warn"
    assert signal["detail"].startswith("4 of 5 commits are dated before this repository was created on GitHub (2024-04-01).")


def test_history_after_repo_creation_is_ok() -> None:
    signal = _repo_signals(
        created_at="2024-04-01T00:00:00Z",
        user_commits=_commits("2024-04-01", "2024-04-03", "2024-04-09"),
    )["history_import"]
    assert signal["status"] == "ok"


def test_commit_timing_signals_are_unknown_without_data() -> None:
    signals = _repo_signals()
    assert signals["commit_spread"]["status"] == "unknown"
    assert signals["history_import"]["status"] == "unknown"
    # Commits but no repository size or creation date: still unknown, never a crash.
    signals = _repo_signals(user_commits=_commits("2024-03-05"), user_commits_complete=True)
    assert signals["commit_spread"]["status"] == "unknown"
    assert signals["history_import"]["status"] == "unknown"
    signals = _repo_signals(user_commits=[{"sha": "x", "author_date": None}], created_at="not a date")
    assert signals["commit_spread"]["status"] == "unknown"
    assert signals["history_import"]["status"] == "unknown"


def test_ingestion_records_user_commit_dates(monkeypatch) -> None:
    from app.services import github_ingest

    page = [{"sha": "a1", "commit": {"author": {"date": "2024-01-01T00:00:00Z"},
                                     "committer": {"date": "2024-01-02T00:00:00Z"}}}]
    monkeypatch.setattr(github_ingest, "_request", lambda client, url, **kwargs: page)
    commits, complete = github_ingest._user_commits(object(), "student", "demo-api", "student")
    assert commits == [{"sha": "a1", "author_date": "2024-01-01T00:00:00Z", "committer_date": "2024-01-02T00:00:00Z"}]
    assert complete is True

    def fail(*_args, **_kwargs):
        raise github_ingest.GitHubIngestError("409 empty repository")

    monkeypatch.setattr(github_ingest, "_request", fail)
    assert github_ingest._user_commits(object(), "student", "demo-api", "student") == (None, False)


def test_github_cache_without_schema_version_is_expired(monkeypatch, tmp_path) -> None:
    import json
    import time

    from app.services import github_ingest

    cache_file = tmp_path / "github-student.json"
    monkeypatch.setattr(github_ingest, "_cache_path", lambda username: cache_file)

    class Refetched(Exception):
        pass

    def no_network(*_args, **_kwargs):
        raise Refetched

    monkeypatch.setattr(github_ingest, "_request", no_network)
    cache_file.write_text(json.dumps({"cached_at": time.time(), "data": {"repos": "old"}}), encoding="utf-8")
    with pytest.raises(Refetched):
        github_ingest.ingest_github("student")
    cache_file.write_text(json.dumps({
        "schema_version": github_ingest.CACHE_SCHEMA_VERSION, "cached_at": time.time(), "data": {"repos": "new"},
    }), encoding="utf-8")
    assert github_ingest.ingest_github("student") == {"repos": "new"}


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
    repo["created_at"] = "2024-01-01T00:00:00Z"
    repo["user_commits"] = _commits("2024-01-02", "2024-01-09", "2024-02-01")
    repo["user_commits_complete"] = True
    repo["source_file_count"] = 20
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
        authored_paths={"backend/Dockerfile": True},
    )
    assert levels["docker"] == "Used"


def test_non_github_ci_counts_as_cicd_evidence() -> None:
    levels = _levels_with(
        tree=["app.py", ".travis.yml"],
        config_files=[".travis.yml"],
        config_contents={".travis.yml": "language: python\n"},
        authored_paths={".travis.yml": True},
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


COPIED_LINES = [
    "from flask import Flask",
    "app = Flask(__name__)",
    "@app.route('/invoices/<int:invoice_id>/total')",
    "def invoice_total(invoice_id):",
    "    return calculate_invoice_total(load_items(invoice_id), tax_rate=0.18)",
]
OLDER_REPO_URL = "https://github.com/original/invoices/blob/main/app.py"


def copy_ingestion(**repo_overrides: object) -> dict:
    data = sample_ingestion()
    repo = data["repos"][0]
    repo["source_files"]["app.py"].update(
        content="\n".join(COPIED_LINES), lines=COPIED_LINES, line_count=len(COPIED_LINES)
    )
    repo["created_at"] = "2024-06-01T00:00:00Z"
    repo.update(repo_overrides)
    return data


def run_copy_check(
    monkeypatch, ingestion: dict, *, other_created: str, second_found: bool = True,
    search_error: bool = False, resume_skills: list | None = None,
) -> dict:
    from app.services import copy_check
    from app.services.verifier import exclude_confirmed_copies

    queries: list[str] = []

    def fake_search(client, query):
        queries.append(query)
        if search_error:
            return None
        if " repo:" in query:
            return {"total_count": int(second_found), "items": []}
        return {"total_count": 1, "items": [{
            "repository": {"full_name": "original/invoices", "owner": {"login": "original"}, "fork": False},
            "html_url": OLDER_REPO_URL,
        }]}

    monkeypatch.setattr(copy_check, "GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(copy_check, "_code_search", fake_search)
    monkeypatch.setattr(copy_check, "_repo_created_at", lambda client, name: other_created)
    verified = verify_claims([claim(end_line=5)], ingestion, resume_skills=resume_skills)
    copy_check.add_copy_signals(verified["skills"], "student", {"demo-api": "2024-06-01T00:00:00Z"})
    exclude_confirmed_copies(verified, ingestion["repos"])
    verified["queries"] = queries
    return verified


def _skill(verified: dict, skill_id: str) -> dict | None:
    return next((item for item in verified["skills"] if item["skill_id"] == skill_id), None)


def _copy_signal(skill: dict) -> dict:
    code = next(item for item in skill["evidence"] if item["type"] == "code")
    return next(signal for signal in code["integrity"] if signal["id"] == "copy_match")


def test_confirmed_copy_in_older_repo_is_excluded_and_level_drops(monkeypatch) -> None:
    verified = run_copy_check(monkeypatch, copy_ingestion(), other_created="2023-01-01T00:00:00Z")
    flask = _skill(verified, "flask")
    assert flask["level"] == "Used"  # was Demonstrated; only requirements.txt remains
    assert not [item for item in flask["evidence"] if item["type"] == "code"]
    copied = [item for item in verified["rejected_claims"] if item["kind"] == "copied"]
    assert len(copied) == 1
    assert copied[0]["failed_checks"] == ["not_copied"]
    assert copied[0]["source_url"] == OLDER_REPO_URL
    assert copied[0]["claim"]["file_path"] == "app.py"
    assert "original/invoices, created 2023-01-01" in copied[0]["reason"]
    assert "(created 2024-06-01)" in copied[0]["reason"]
    # The second search uses a different line, restricted to the older repo.
    assert len(verified["queries"]) == 2 and verified["queries"][1].endswith("repo:original/invoices")
    assert verified["queries"][0].split('"')[1] != verified["queries"][1].split('"')[1]


def test_older_repo_without_second_line_match_is_only_a_warning(monkeypatch) -> None:
    verified = run_copy_check(
        monkeypatch, copy_ingestion(), other_created="2023-01-01T00:00:00Z", second_found=False,
    )
    flask = _skill(verified, "flask")
    assert flask["level"] == "Demonstrated"
    signal = _copy_signal(flask)
    assert signal["status"] == "warn" and not signal.get("confirmed_copy")
    assert not [item for item in verified["rejected_claims"] if item.get("kind") == "copied"]


def test_newer_repo_match_still_counts(monkeypatch) -> None:
    verified = run_copy_check(monkeypatch, copy_ingestion(), other_created="2025-01-01T00:00:00Z")
    flask = _skill(verified, "flask")
    assert flask["level"] == "Demonstrated"
    assert _copy_signal(flask)["status"] == "ok"


def test_search_error_still_counts_with_unknown_signal(monkeypatch) -> None:
    verified = run_copy_check(
        monkeypatch, copy_ingestion(), other_created="2023-01-01T00:00:00Z", search_error=True,
    )
    flask = _skill(verified, "flask")
    assert flask["level"] == "Demonstrated"
    assert _copy_signal(flask)["status"] == "unknown"


def test_skill_losing_only_code_evidence_drops_from_applied_to_used(monkeypatch) -> None:
    ingestion = copy_ingestion(test_files=[], config_files=[], config_contents={})
    verified = run_copy_check(monkeypatch, ingestion, other_created="2023-01-01T00:00:00Z")
    assert _skill(verified, "flask")["level"] == "Used"


def test_skill_losing_all_evidence_is_removed_or_kept_as_claimed(monkeypatch) -> None:
    ingestion = copy_ingestion(dependency_contents={})
    verified = run_copy_check(monkeypatch, ingestion, other_created="2023-01-01T00:00:00Z")
    assert _skill(verified, "flask") is None
    verified = run_copy_check(
        monkeypatch, copy_ingestion(dependency_contents={}), other_created="2023-01-01T00:00:00Z",
        resume_skills=[{"skill_id": "flask", "mention": "Flask"}],
    )
    flask = _skill(verified, "flask")
    assert flask["level"] == "Claimed" and flask["evidence"] == []


def test_failed_claims_are_tagged_unsupported() -> None:
    result = verify_claims([claim(file_path="missing.py")], sample_ingestion())
    assert result["rejected_claims"][0]["kind"] == "unsupported"


def test_language_claim_needs_matching_file_extension() -> None:
    data = plain_python_ingestion()
    assert verify_one_claim(claim(skill_id="python", file_path="util.py", start_line=1, end_line=4), data)["verified"]
    assert not verify_one_claim(claim(skill_id="javascript", file_path="util.py", start_line=1, end_line=4), data)["verified"]
