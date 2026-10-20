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
    assert levels["flask"] == "Applied"


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


def test_code_citations_carry_an_evidence_record() -> None:
    from app.config import VERIFIER_VERSION
    from app.services.verifier import snippet_sha256

    data = two_module_ingestion()
    data["repos"][0]["source_files"]["billing/engine.py"]["retrieved_at"] = "2026-10-15T09:00:00+00:00"
    skills = {item["skill_id"]: item for item in verify_claims(TWO_MODULE_CLAIMS, data)["skills"]}
    pandas_code = next(item for item in skills["pandas"]["evidence"] if item["type"] == "code")
    record = pandas_code["record"]
    assert record == {
        "commit_sha": "abc123", "path": "billing/engine.py", "start_line": 1, "end_line": 4,
        "snippet_sha256": snippet_sha256("\n".join(ENGINE_LINES)),
        "retrieved_at": "2026-10-15T09:00:00+00:00", "verifier_version": VERIFIER_VERSION,
        "checks": pandas_code["checks"],
        "rules_applied": ["applied:code_citation", "demonstrated:test_name_match"],
    }
    flask_code = next(item for item in skills["flask"]["evidence"] if item["type"] == "code")
    assert flask_code["record"]["rules_applied"] == ["applied:code_citation"]


def test_snippet_hash_is_deterministic_and_changes_with_any_line() -> None:
    from app.services.verifier import snippet_sha256

    snippet = "\n".join(ENGINE_LINES)
    assert snippet_sha256(snippet) == snippet_sha256("\n".join(list(ENGINE_LINES)))
    assert len(snippet_sha256(snippet)) == 64
    changed = ENGINE_LINES[:-1] + ["    return pd.read_csv(path, sep=';')"]
    assert snippet_sha256("\n".join(changed)) != snippet_sha256(snippet)
    assert snippet_sha256(snippet + " ") != snippet_sha256(snippet)


def test_verified_code_without_a_targeting_test_is_applied() -> None:
    # The repo has tests/test_app.py, but "app" is a generic stem, so it targets nothing specific.
    result = verify_claims([claim()], sample_ingestion())
    flask = next(skill for skill in result["skills"] if skill["skill_id"] == "flask")
    assert flask["level"] == "Applied"
    assert flask["evidence"][0]["checks"]["authored"] is True
    assert "demonstrated_by" not in flask["evidence"][0]


ENGINE_LINES = ["import pandas as pd", "", "def load(path):", "    return pd.read_csv(path)"]
SERVER_LINES = ["from flask import Flask", "server = Flask(__name__)", "", "@server.route('/')", "def home():", "    return 'ok'"]


def two_module_ingestion(**repo_overrides: object) -> dict:
    """billing/engine.py (pandas) and web/server.py (Flask), with a test for the engine only."""
    data = sample_ingestion()
    repo = data["repos"][0]
    repo["source_files"] = {
        "billing/engine.py": {"content": "\n".join(ENGINE_LINES), "lines": ENGINE_LINES,
                              "authored_shas": ["s1"], "line_count": len(ENGINE_LINES)},
        "web/server.py": {"content": "\n".join(SERVER_LINES), "lines": SERVER_LINES,
                          "authored_shas": ["s1"], "line_count": len(SERVER_LINES)},
    }
    repo["tree"] = ["billing/engine.py", "web/server.py", "tests/test_engine.py", "Dockerfile", "requirements.txt"]
    repo["test_files"] = ["tests/test_engine.py"]
    repo["test_authored"] = {"tests/test_engine.py": True}  # the student committed to the test
    repo.update(repo_overrides)
    return data


TWO_MODULE_CLAIMS = [
    claim(skill_id="pandas", file_path="billing/engine.py", start_line=1, end_line=4),
    claim(skill_id="flask", file_path="web/server.py", start_line=1, end_line=6),
]


def test_demonstrated_needs_a_test_that_targets_the_cited_module() -> None:
    skills = {item["skill_id"]: item for item in verify_claims(TWO_MODULE_CLAIMS, two_module_ingestion())["skills"]}
    assert skills["pandas"]["level"] == "Demonstrated"
    code = next(item for item in skills["pandas"]["evidence"] if item["type"] == "code")
    assert code["demonstrated_by"] == {"path": "tests/test_engine.py", "match": "name"}
    # Same repo, tests and a Dockerfile exist, but nothing targets web/server.py.
    assert skills["flask"]["level"] == "Applied"
    flask_code = next(item for item in skills["flask"]["evidence"] if item["type"] == "code")
    assert "demonstrated_by" not in flask_code
    assert flask_code["build_config_present"] is True  # shown as context only


def test_a_dockerfile_or_ci_alone_no_longer_upgrades_anything() -> None:
    data = two_module_ingestion(test_files=[], config_files=["Dockerfile", ".github/workflows/ci.yml"])
    levels = {item["skill_id"]: item["level"] for item in verify_claims(TWO_MODULE_CLAIMS, data)["skills"]}
    assert levels["pandas"] == "Applied" and levels["flask"] == "Applied"


def test_import_match_in_python_tests() -> None:
    data = two_module_ingestion(
        test_files=["tests/test_billing.py"],
        test_contents={"tests/test_billing.py": "import pytest\nfrom billing.engine import load\n\ndef test_billing():\n    assert load('test.csv') is not None\n"},
        test_authored={"tests/test_billing.py": True},
    )
    pandas = next(item for item in verify_claims(TWO_MODULE_CLAIMS, data)["skills"] if item["skill_id"] == "pandas")
    assert pandas["level"] == "Demonstrated"
    code = next(item for item in pandas["evidence"] if item["type"] == "code")
    assert code["demonstrated_by"] == {"path": "tests/test_billing.py", "match": "import"}


def test_import_match_in_javascript_tests() -> None:
    from app.services.verifier import targeting_test

    for content in ("const engine = require('../engine');", "import { run } from '../lib/engine.js';",
                    'import calc from "./engine"'):
        repo = {"test_files": ["src/__tests__/calc.test.js"], "test_contents": {"src/__tests__/calc.test.js": content}}
        assert targeting_test("src/engine.js", repo) == {"path": "src/__tests__/calc.test.js", "match": "import"}, content
    # A longer module name that merely contains the stem is not a match.
    repo = {"test_files": ["src/__tests__/calc.test.js"],
            "test_contents": {"src/__tests__/calc.test.js": "import x from '../myengine';\nimport y from 'engine-room';"}}
    assert targeting_test("src/engine.js", repo) is None


def test_name_match_variants() -> None:
    from app.services.verifier import targeting_test

    for test_path in ("tests/test_engine.py", "engine_test.py", "src/engine.test.js", "src/engine.spec.ts",
                      "tests/test_engine_edge_cases.py"):
        assert targeting_test("pkg/engine.py", {"test_files": [test_path]}) == {"path": test_path, "match": "name"}
    assert targeting_test("pkg/engine.py", {"test_files": ["tests/test_reengine.py"]}) is None


def test_generic_and_short_stems_never_match() -> None:
    from app.services.verifier import targeting_test

    for cited, test_path, content in (
        ("app.py", "tests/test_app.py", "from app import app"),
        ("src/index.js", "src/index.test.js", "import x from './index'"),
        ("pkg/__init__.py", "tests/test_init.py", "import pkg"),
        ("main.go", "main_test.go", ""),
        ("lib/utils.py", "tests/test_utils.py", "from lib.utils import x"),
        ("db.py", "tests/test_db.py", "import db"),
    ):
        repo = {"test_files": [test_path], "test_contents": {test_path: content}}
        assert targeting_test(cited, repo) is None, cited


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
    source["commit_verification"] = {"verified": True, "reason": "valid"}
    repo["commit_counts"] = {"total": 24, "by_user": 24}
    repo["created_at"] = "2024-01-01T00:00:00Z"
    repo["user_commits"] = _commits("2024-01-02", "2024-01-09", "2024-02-01")
    repo["user_commits_complete"] = True
    repo["source_file_count"] = 20
    signals = _signals(ingestion)
    assert {item["status"] for item in signals.values()} == {"ok"}


def test_copy_check_compares_when_the_files_first_appeared(monkeypatch) -> None:
    from app.services import copy_check

    monkeypatch.setattr(
        copy_check, "_search",
        lambda client, line, username: {"total": 1, "matches": [{"repo": "x/y", "url": "u", "path": "a.py"}]},
    )
    evidence = {"snippet": "    return calculate_invoice_total(order_items, tax_rate)"}
    mine = "2024-06-01T00:00:00Z"
    monkeypatch.setattr(copy_check, "_file_first_seen", lambda client, name, path: "2023-01-01T00:00:00Z")
    signal = copy_check.copy_signal(evidence, object(), "student", mine)
    assert signal["status"] == "warn" and signal["matches"] and not signal.get("earlier_source")
    # The other file appeared later: it may have been taken from the student, so it's fine.
    monkeypatch.setattr(copy_check, "_file_first_seen", lambda client, name, path: "2025-01-01T00:00:00Z")
    assert copy_check.copy_signal(evidence, object(), "student", mine)["status"] == "ok"
    # Same day is not "earlier" (1-day margin).
    monkeypatch.setattr(copy_check, "_file_first_seen", lambda client, name, path: "2024-05-31T12:00:00Z")
    assert copy_check.copy_signal(evidence, object(), "student", mine)["status"] == "ok"
    # Without the student's file date, the order can't be judged: advisory warning only.
    signal = copy_check.copy_signal(evidence, object(), "student")
    assert signal["status"] == "warn" and not signal.get("earlier_source")
    assert copy_check.copy_signal({"snippet": "import os"}, object(), "student")["status"] == "unknown"
    assert copy_check.copy_signal(evidence, None, "student")["status"] == "unknown"
    assert copy_check.LABEL == "Earlier public source"


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
    ingestion["repos"][0]["user_commits"] = _commits("2024-01-01", "2024-01-05")
    ingestion["repos"][0]["user_commits_complete"] = True
    result = verify_claims([], ingestion)
    git = next(item for item in result["skills"] if item["skill_id"] == "git")
    assert git["level"] == "Used"
    history = next(item for item in git["evidence"] if item["file_path"] == "(commit history)")
    assert history["type"] == "config"
    assert history["url"] == "https://github.com/student/demo-api/commits?author=student"
    assert history["explanation"] == "Git: 2 commit(s) authored by you in this repository."
    assert history["checks"] == {"file_exists": True, "authored": True, "not_fork": True}


def test_git_is_used_when_the_student_has_commits() -> None:
    data = sample_ingestion()
    data["repos"][0]["commit_counts"] = {"total": 12, "by_user": 9}
    skills = {item["skill_id"]: item for item in verify_claims([], data)["skills"]}
    assert skills["git"]["level"] == "Used"
    assert "9 commit(s)" in skills["git"]["evidence"][0]["explanation"]


def test_git_is_not_evidenced_for_a_fork() -> None:
    assert "git" not in _levels_with(is_fork=True)
    data = sample_ingestion(is_fork=True)
    data["repos"][0]["commit_counts"] = {"total": 12, "by_user": 9}
    data["repos"][0]["user_commits"] = _commits("2024-01-01")
    skills = {item["skill_id"]: item for item in verify_claims([], data)["skills"]}
    assert "git" not in skills


def test_test_file_evidence_is_capped() -> None:
    data = sample_ingestion()
    data["repos"][0]["test_files"] = [f"tests/test_{index}.py" for index in range(40)]
    skills = {item["skill_id"]: item for item in verify_claims([], data)["skills"]}
    assert len(skills["unit-testing"]["evidence"]) == 3
    item = skills["unit-testing"]["evidence"][0]
    assert set(item["checks"]) == {"file_exists", "authored", "not_fork"}
    assert item["checks"]["authored"] is False
    assert item["url"] == "https://github.com/student/demo-api/blob/abc123/tests/test_0.py"
    assert item["explanation"] == "Test file present in the repository."
    assert skills["unit-testing"]["level"] == "Claimed"


def test_authored_test_file_is_prioritized_before_evidence_cap() -> None:
    data = sample_ingestion()
    repo = data["repos"][0]
    repo["test_files"] = [f"tests/test_{index}.py" for index in range(4)]
    repo["tree"].extend(repo["test_files"])
    repo["test_authored"] = {path: path == "tests/test_3.py" for path in repo["test_files"]}
    repo["test_contents"] = {"tests/test_3.py": "def test_authored():\n    assert True\n"}

    skills = {item["skill_id"]: item for item in verify_claims([], data)["skills"]}
    evidence = skills["unit-testing"]["evidence"]
    authored = next(item for item in evidence if item["file_path"] == "tests/test_3.py")
    assert authored["checks"]["authored"] is True
    assert skills["unit-testing"]["level"] == "Used"
    assert len(evidence) == 3


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
    # The file already cited as code is not listed again as a test-file item.
    assert [item["file_path"] for item in config] == ["tests/test_more.py"]
    assert skill["level"] == "Applied"  # test code itself, not targeted by another test


def test_unauthored_test_file_does_not_count_towards_used() -> None:
    ingestion = sample_ingestion()
    repo = ingestion["repos"][0]
    lines = ["def test_health():", "    assert True"]
    repo["source_files"]["tests/test_app.py"] = {
        "content": "\n".join(lines), "lines": lines, "authored_shas": [], "line_count": 2,
    }
    skill = next(
        item for item in verify_claims([], ingestion)["skills"] if item["skill_id"] == "unit-testing"
    )
    test_evidence = next(item for item in skill["evidence"] if item.get("test_file_group"))
    assert test_evidence["checks"]["authored"] is False
    assert skill["level"] == "Claimed"


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


def test_skills_and_requirements_carry_display_names() -> None:
    from app.services.role_match import match_role

    skills = verify_claims([claim()], sample_ingestion())["skills"]
    names = {item["skill_id"]: item["name"] for item in skills}
    assert names["flask"] == "Flask" and names["unit-testing"] == "Unit Testing"
    requirements = match_role(skills, "backend-intern")["requirements"]
    assert all(item["name"] for item in requirements)
    assert next(item for item in requirements if item["skill_id"] == "rest-api")["name"] == "REST API design"


def test_nested_dockerfile_is_docker_evidence_but_does_not_upgrade_other_skills() -> None:
    ingestion = sample_ingestion()
    repo = ingestion["repos"][0]
    repo.update(
        test_files=[], tree=["app.py", "deploy/docker/Dockerfile"],
        config_files=["deploy/docker/Dockerfile"], config_contents={"deploy/docker/Dockerfile": "FROM python:3.11\n"},
        authored_paths={"deploy/docker/Dockerfile": True},
    )
    skills = {item["skill_id"]: item for item in verify_claims([claim()], ingestion)["skills"]}
    assert skills["flask"]["level"] == "Applied"
    assert skills["flask"]["evidence"][0]["build_config_present"] is True
    assert skills["docker"]["level"] == "Used"


def test_resume_mention_lines_are_redacted() -> None:
    from app.services.resume_parse import match_resume_skills

    text = "Contact jane.doe@example.com +91 98765 43210 | Python, Docker\nPython Django"
    mentions = {item["skill_id"]: item["mention"] for item in match_resume_skills(text)}
    assert "@" not in mentions["python"] and "98765" not in mentions["python"]
    assert mentions["python"] == "Python"
    assert mentions["django"] == "Django"


def test_stored_mentions_are_redacted_on_recheck() -> None:
    from app.services.pipeline import _redact_mentions

    redacted = _redact_mentions([{"skill_id": "aws", "mention": "AWS - call +1 (555) 123-4567 or a@b.io"}])
    assert "555" not in redacted[0]["mention"] and "a@b.io" not in redacted[0]["mention"]


def test_go_alias_does_not_match_the_everyday_word() -> None:
    from app.services.framework import skill_map
    from app.services.resume_parse import match_resume_skills

    assert "go" not in skill_map()["go"]["aliases"]
    assert not [item for item in match_resume_skills("I like to go hiking") if item["skill_id"] == "go"]


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
OLDER_REPO_URL = "https://github.com/original/invoices/blob/main/src/app.py"
CANDIDATE_FIRST_SEEN = "2024-06-01T00:00:00Z"


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
    monkeypatch, ingestion: dict, *, other_file_seen: str | None, second_found: bool = True,
    search_error: bool = False, resume_skills: list | None = None,
    candidate_first_seen: str | None = CANDIDATE_FIRST_SEEN, claims: list | None = None,
) -> dict:
    from app.services import copy_check
    from app.services.verifier import exclude_uncounted_citations

    queries: list[str] = []
    checked: list[tuple[str, str, str]] = []

    def fake_search(client, query):
        queries.append(query)
        if search_error:
            return None
        return {"total_count": 1, "items": [{
            "repository": {"full_name": "original/invoices", "owner": {"login": "original"}, "fork": False},
            "html_url": OLDER_REPO_URL, "path": "src/app.py",
        }]}

    def fake_contains(client, full_name, path, line):
        checked.append((full_name, path, line))
        return second_found

    monkeypatch.setattr(copy_check, "GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(copy_check, "_code_search", fake_search)
    monkeypatch.setattr(copy_check, "_file_first_seen", lambda client, name, path: other_file_seen)
    monkeypatch.setattr(copy_check, "_file_contains", fake_contains)
    verified = verify_claims(claims or [claim(end_line=5)], ingestion, resume_skills=resume_skills)
    first_seen = {"demo-api/app.py": candidate_first_seen} if candidate_first_seen else {}
    copy_check.add_copy_signals(verified["skills"], "student", first_seen)
    exclude_uncounted_citations(verified, ingestion["repos"])
    verified["queries"] = queries
    verified["checked"] = checked
    return verified


def _skill(verified: dict, skill_id: str) -> dict | None:
    return next((item for item in verified["skills"] if item["skill_id"] == skill_id), None)


def _copy_signal(skill: dict) -> dict:
    code = next(item for item in skill["evidence"] if item["type"] == "code")
    return next(signal for signal in code["integrity"] if signal["id"] == "copy_match")


def _excluded(verified: dict) -> list[dict]:
    return [item for item in verified["rejected_claims"] if item.get("kind") == "earlier_source"]


def test_earlier_file_with_both_lines_is_excluded_with_neutral_wording(monkeypatch) -> None:
    verified = run_copy_check(monkeypatch, copy_ingestion(), other_file_seen="2023-01-01T00:00:00Z")
    flask = _skill(verified, "flask")
    assert flask["level"] == "Used"  # was Applied; only requirements.txt remains
    assert not [item for item in flask["evidence"] if item["type"] == "code"]
    excluded = _excluded(verified)
    assert len(excluded) == 1
    assert excluded[0]["failed_checks"] == ["earlier_source"]
    assert excluded[0]["source_url"] == OLDER_REPO_URL
    assert excluded[0]["source_first_seen"] == "2023-01-01T00:00:00Z"
    assert excluded[0]["candidate_first_seen"] == CANDIDATE_FIRST_SEEN
    assert excluded[0]["url"] == "https://github.com/student/demo-api/blob/abc123/app.py#L1-L5"
    assert excluded[0]["reason"] == (
        "Matches code that appeared in original/invoices on 2023-01-01, before this file's first commit "
        "here (2024-06-01). Not counted as the candidate's own work. This may be legitimate reuse; "
        "review if relevant."
    )
    assert not any(word in excluded[0]["reason"].lower() for word in ("copied", "fraud", "fake", "cheat"))
    # The second line is checked in that same file, and it differs from the searched line.
    assert len(verified["queries"]) == 1
    assert verified["checked"][0][:2] == ("original/invoices", "src/app.py")
    assert verified["checked"][0][2] != verified["queries"][0].split('"')[1]


def test_repo_created_earlier_but_file_added_later_is_not_excluded(monkeypatch) -> None:
    # Whatever the other repo's creation date, only when its *file* appeared matters.
    verified = run_copy_check(monkeypatch, copy_ingestion(), other_file_seen="2025-03-01T00:00:00Z")
    flask = _skill(verified, "flask")
    assert flask["level"] == "Applied"  # still counted
    assert _copy_signal(flask)["status"] == "ok"
    assert not _excluded(verified)


def test_earlier_file_without_second_line_match_is_only_a_warning(monkeypatch) -> None:
    verified = run_copy_check(
        monkeypatch, copy_ingestion(), other_file_seen="2023-01-01T00:00:00Z", second_found=False,
    )
    flask = _skill(verified, "flask")
    assert flask["level"] == "Applied"  # still counted
    signal = _copy_signal(flask)
    assert signal["status"] == "warn" and not signal.get("earlier_source")
    assert not _excluded(verified)


def test_unknown_file_date_is_a_warning_only(monkeypatch) -> None:
    for kwargs in ({"other_file_seen": None}, {"other_file_seen": "2023-01-01T00:00:00Z", "candidate_first_seen": None}):
        verified = run_copy_check(monkeypatch, copy_ingestion(), **kwargs)
        flask = _skill(verified, "flask")
        assert flask["level"] == "Applied"  # still counted
        signal = _copy_signal(flask)
        assert signal["status"] == "warn" and not signal.get("earlier_source")
        assert not _excluded(verified)


def test_search_error_still_counts_with_unknown_signal(monkeypatch) -> None:
    verified = run_copy_check(
        monkeypatch, copy_ingestion(), other_file_seen="2023-01-01T00:00:00Z", search_error=True,
    )
    flask = _skill(verified, "flask")
    assert flask["level"] == "Applied"  # still counted
    assert _copy_signal(flask)["status"] == "unknown"


def test_skill_losing_only_code_evidence_drops_from_applied_to_used(monkeypatch) -> None:
    ingestion = copy_ingestion(test_files=[], config_files=[], config_contents={})
    verified = run_copy_check(monkeypatch, ingestion, other_file_seen="2023-01-01T00:00:00Z")
    assert _skill(verified, "flask")["level"] == "Used"


def test_skill_losing_all_evidence_is_removed_or_kept_as_claimed(monkeypatch) -> None:
    ingestion = copy_ingestion(dependency_contents={})
    verified = run_copy_check(monkeypatch, ingestion, other_file_seen="2023-01-01T00:00:00Z")
    assert _skill(verified, "flask") is None
    verified = run_copy_check(
        monkeypatch, copy_ingestion(dependency_contents={}), other_file_seen="2023-01-01T00:00:00Z",
        resume_skills=[{"skill_id": "flask", "mention": "Flask"}],
    )
    flask = _skill(verified, "flask")
    assert flask["level"] == "Claimed" and flask["evidence"] == []


# Lines 1-2 of COPIED_LINES are boilerplate only (an import and a short line): nothing to search.
BOILERPLATE_CLAIM = {"start_line": 1, "end_line": 2}
DISTINCTIVE_CLAIM = {"start_line": 3, "end_line": 5}


def test_confirmed_earlier_source_excludes_every_citation_in_that_file(monkeypatch) -> None:
    import app.main as main_module

    verified = run_copy_check(
        monkeypatch, copy_ingestion(), other_file_seen="2023-01-01T00:00:00Z",
        claims=[claim(**BOILERPLATE_CLAIM), claim(**DISTINCTIVE_CLAIM)],
    )
    flask = _skill(verified, "flask")
    assert flask["level"] == "Used"  # the boilerplate citation no longer keeps it at Applied
    assert not _code_items(flask)
    excluded = sorted(_excluded(verified), key=lambda item: item["claim"]["start_line"])
    assert [(item["claim"]["start_line"], item["claim"]["end_line"]) for item in excluded] == [(1, 2), (3, 5)]
    boilerplate, confirmed = excluded
    assert boilerplate["failed_checks"] == ["earlier_source"]
    assert boilerplate["source_url"] == confirmed["source_url"] == OLDER_REPO_URL
    assert boilerplate["reason"] == (
        f"Excluded because another citation in this file (lines 3-5) matched an earlier public source. {confirmed['reason']}"
    )
    assert len(verified["queries"]) == 1  # no extra searches for the boilerplate citation
    # Students see the same neutral outcome for both.
    student = main_module.student_view({"id": "p", **verified})["rejected_claims"]
    assert [item["reason"] for item in student if item["kind"] == "earlier_source"] == [main_module.STUDENT_EXCLUDED_REASON] * 2


def test_confirmed_earlier_source_does_not_affect_another_file_in_the_repo(monkeypatch) -> None:
    ingestion = copy_ingestion()
    repo = ingestion["repos"][0]
    other_lines = COPIED_LINES[:2]
    repo["source_files"]["web/setup.py"] = {
        "content": "\n".join(other_lines), "lines": other_lines, "authored_shas": ["def456"], "line_count": 2,
    }
    repo["tree"].append("web/setup.py")
    verified = run_copy_check(
        monkeypatch, ingestion, other_file_seen="2023-01-01T00:00:00Z",
        claims=[claim(**DISTINCTIVE_CLAIM), claim(file_path="web/setup.py", start_line=1, end_line=2)],
    )
    flask = _skill(verified, "flask")
    assert flask["level"] == "Applied"  # web/setup.py still counts
    assert [item["file_path"] for item in _code_items(flask)] == ["web/setup.py"]
    assert not _copy_signal(flask).get("earlier_source")
    assert [item["claim"]["file_path"] for item in _excluded(verified)] == ["app.py"]


def test_one_line_advisory_match_does_not_spread_to_the_file(monkeypatch) -> None:
    verified = run_copy_check(
        monkeypatch, copy_ingestion(), other_file_seen="2023-01-01T00:00:00Z", second_found=False,
        claims=[claim(**BOILERPLATE_CLAIM), claim(**DISTINCTIVE_CLAIM)],
    )
    flask = _skill(verified, "flask")
    assert flask["level"] == "Applied"
    assert len(_code_items(flask)) == 2
    assert not _excluded(verified)
    signals = {
        item["start_line"]: next(s for s in item["integrity"] if s["id"] == "copy_match") for item in _code_items(flask)
    }
    assert signals[1]["detail"] == "No distinctive line in the citation to search for."
    assert signals[3]["status"] == "warn" and not signals[3].get("earlier_source")


ROUTES_LINES = [
    "from flask import Flask",
    "app = Flask(__name__)",
    "",
    "@app.route('/orders')",
    "def orders():",
    "    items = load_orders()",
    "    return {'orders': items}",
    "",
    "@app.route('/health')",
    "def health():",
    "    return {'status': 'ok'}",
]


def blame_ingestion(blame: list | None) -> dict:
    """web/routes.py (11 Flask lines, committed to by the student) with tests/test_routes.py targeting it."""
    data = sample_ingestion()
    repo = data["repos"][0]
    repo["source_files"]["web/routes.py"] = {
        "content": "\n".join(ROUTES_LINES), "lines": ROUTES_LINES, "authored_shas": ["def456"],
        "line_count": len(ROUTES_LINES), "blame": blame,
    }
    repo["tree"] += ["web/routes.py", "tests/test_routes.py"]
    repo["test_files"] = ["tests/test_routes.py"]
    repo["test_authored"] = {"tests/test_routes.py": True}
    return data


def run_with_blame(blame: list | None, **repo_overrides: object) -> dict:
    from app.services.verifier import exclude_uncounted_citations

    data = blame_ingestion(blame)
    data["repos"][0].update(repo_overrides)
    verified = verify_claims([claim(file_path="web/routes.py", start_line=1, end_line=11)], data,
                             resume_skills=[{"skill_id": "aws", "mention": "AWS"}])
    return exclude_uncounted_citations(verified, data["repos"])


def _code_items(skill: dict) -> list[dict]:
    return [item for item in skill["evidence"] if item["type"] == "code"]


def test_citation_with_no_candidate_written_lines_is_excluded_and_level_recomputed() -> None:
    verified = run_with_blame([[1, 11, "karpathy"]])
    flask = _skill(verified, "flask")
    assert flask["level"] == "Used"  # was Demonstrated; only the authored requirements.txt remains
    assert not _code_items(flask)
    excluded = [item for item in verified["rejected_claims"] if item["kind"] == "not_written_by_candidate"]
    assert len(excluded) == 1
    assert excluded[0]["reason"] == "None of the cited lines were last changed in your commits."
    assert excluded[0]["failed_checks"] == ["not_written_by_candidate"]
    assert excluded[0]["lines_written"] == {"written": 0, "cited": 11}
    assert excluded[0]["url"].endswith("web/routes.py#L1-L11")
    # With nothing else supporting Flask (and no resume mention), the skill disappears.
    assert _skill(run_with_blame([[1, 11, "karpathy"]], dependency_contents={}), "flask") is None


def test_partly_written_citation_counts_but_cannot_be_demonstrated() -> None:
    flask = _skill(run_with_blame([[1, 3, "student"], [4, 11, "karpathy"]]), "flask")
    assert flask["level"] == "Applied"
    code = _code_items(flask)[0]
    assert code["lines_written"] == {"written": 3, "cited": 11}
    assert "demonstrated_by" not in code
    assert code["record"]["rules_applied"] == ["applied:code_citation"]


def test_unknown_blame_counts_normally() -> None:
    for blame in (None, [[1, 11, None]], [[1, 4, "student"], [5, 11, None]]):
        verified = run_with_blame(blame)
        flask = _skill(verified, "flask")
        code = _code_items(flask)[0]
        assert code["lines_written"] is None, blame
        assert next(s for s in code["integrity"] if s["id"] == "line_share")["status"] == "unknown"
        assert not [item for item in verified["rejected_claims"] if item["kind"] == "not_written_by_candidate"]
        assert flask["level"] == "Demonstrated", blame


def _flask_routes(blame: list | None = None, end_line: int = 11, **repo_overrides: object) -> dict:
    data = blame_ingestion(blame)
    data["repos"][0].update(repo_overrides)
    verified = verify_claims([claim(file_path="web/routes.py", start_line=1, end_line=end_line)], data)
    return _skill(verified, "flask")


def test_candidate_committed_test_targeting_authored_code_is_demonstrated() -> None:
    flask = _flask_routes([[1, 11, "student"]])
    assert flask["level"] == "Demonstrated"
    assert _code_items(flask)[0]["demonstrated_by"] == {"path": "tests/test_routes.py", "match": "name"}


def test_test_written_by_someone_else_leaves_the_skill_applied() -> None:
    flask = _flask_routes([[1, 11, "student"]], test_authored={"tests/test_routes.py": False})
    assert flask["level"] == "Applied"
    assert "demonstrated_by" not in _code_items(flask)[0]
    # The same holds for an import match: karpathy's test importing the module doesn't count.
    flask = _flask_routes(
        [[1, 11, "student"]], test_files=["tests/test_api.py"],
        test_contents={"tests/test_api.py": "from web.routes import orders\n"},
        test_authored={"tests/test_api.py": False},
    )
    assert flask["level"] == "Applied"


def test_test_author_lookup_error_leaves_the_skill_applied() -> None:
    # Ingestion leaves a test out of test_authored when its history couldn't be read.
    assert _flask_routes([[1, 11, "student"]], test_authored={})["level"] == "Applied"


def test_low_line_share_with_a_committed_test_stays_applied() -> None:
    # 2 of 10 cited lines (20%) written by the candidate, plus a test they committed to.
    flask = _flask_routes([[1, 2, "student"], [3, 11, "karpathy"]], end_line=10)
    assert _code_items(flask)[0]["lines_written"] == {"written": 2, "cited": 10}
    assert flask["level"] == "Applied"


def test_ingestion_records_who_committed_to_each_read_test_file(monkeypatch) -> None:
    from app.services import github_ingest

    def authorship(client, owner, repo, path, username):
        if path == "tests/test_broken.py":
            raise github_ingest.GitHubIngestError("GitHub API returned 500")
        return ["sha1"] if path == "tests/test_mine.py" else []

    monkeypatch.setattr(github_ingest, "_file_authorship", authorship)
    source_files = {"tests/test_read.py": {"authored_shas": ["sha2"]}}
    result = github_ingest._test_authorship(
        object(), "student", "demo", ["tests/test_mine.py", "tests/test_theirs.py", "tests/test_broken.py",
                                      "tests/test_read.py"], source_files, "student",
    )
    assert result == {"tests/test_mine.py": True, "tests/test_theirs.py": False, "tests/test_read.py": True}


def test_failed_claims_are_tagged_unsupported() -> None:
    result = verify_claims([claim(file_path="missing.py")], sample_ingestion())
    assert result["rejected_claims"][0]["kind"] == "unsupported"


def test_language_claim_needs_matching_file_extension() -> None:
    data = plain_python_ingestion()
    assert verify_one_claim(claim(skill_id="python", file_path="util.py", start_line=1, end_line=4), data)["verified"]
    assert not verify_one_claim(claim(skill_id="javascript", file_path="util.py", start_line=1, end_line=4), data)["verified"]


def test_ci_evidence_verifies_successful_ci_runs() -> None:
    from app.services.verifier import _deterministic_evidence
    from app.services.framework import get_skills
    cicd_skill = next(s for s in get_skills() if s["id"] == "cicd")
    repo = {
        "name": "demo",
        "full_name": "user/demo",
        "head_sha": "abc1234567",
        "tree": [".github/workflows/ci.yml"],
        "config_files": [".github/workflows/ci.yml"],
        "config_contents": {".github/workflows/ci.yml": "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"},
        "authored_paths": {".github/workflows/ci.yml": True},
        "ci_status": {
            "has_successful_run": True,
            "total_checks": 1,
            "successful_checks": 1,
            "failed_checks": 0,
            "in_progress_checks": 0,
            "status": "passed",
            "summary": "All 1 CI check(s) passed",
        },
    }
    evidence = _deterministic_evidence(cicd_skill, [repo], username="user")
    assert len(evidence) == 1
    item = evidence[0]
    assert "ci_run_verified" not in item["checks"]
    assert item["ci_run"] == {"verified": True, "status": "passed"}
    assert "All 1 CI check(s) passed" in item["explanation"]


def test_ci_evidence_flags_failed_or_missing_runs() -> None:
    from app.services.verifier import _deterministic_evidence
    from app.services.framework import get_skills
    cicd_skill = next(s for s in get_skills() if s["id"] == "cicd")
    repo = {
        "name": "demo",
        "full_name": "user/demo",
        "head_sha": "def7890123",
        "tree": [".github/workflows/ci.yml"],
        "config_files": [".github/workflows/ci.yml"],
        "config_contents": {".github/workflows/ci.yml": "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"},
        "authored_paths": {".github/workflows/ci.yml": True},
        "ci_status": {
            "has_successful_run": False,
            "total_checks": 2,
            "successful_checks": 1,
            "failed_checks": 1,
            "in_progress_checks": 0,
            "status": "failed",
            "summary": "1 of 2 CI check(s) failed",
        },
    }
    evidence = _deterministic_evidence(cicd_skill, [repo], username="user")
    assert len(evidence) == 1
    item = evidence[0]
    assert "ci_run_verified" not in item["checks"]
    assert all(item["checks"].values())
    assert item["ci_run"] == {"verified": False, "status": "failed"}
    assert "failed" in item["explanation"]
