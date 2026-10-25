import copy
import socket
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.main as main_module
from app import config
from app.services import pipeline, report_parse, storage
from app.services.gemini import GeminiUnavailableError
from app.services.role_match import match_role

REPORT_TEXT = """Smart Parking System - Final Year Project Report
Author email: student@example.com
We built the backend API in Python with Flask and wrote pytest tests for the booking engine.
The services run in Docker containers and were deployed on a Kubernetes cluster.
Code: https://github.com/Student-Dev/parking-api and https://github.com/someone-else/parking-ui.git
See also github.com/orgs/acme and https://github.com/student-dev/parking-api/tree/main."""

GEMINI_CLAIMS = [
    {"skill_id": "python", "quote": "backend API in Python"},
    {"skill_id": "flask", "quote": "Python with Flask"},
    {"skill_id": "python", "quote": "in Python"},  # duplicate: the first quote is kept
    {"skill_id": "docker", "quote": "run in Docker containers"},
    {"skill_id": "kubernetes", "quote": "on a Kubernetes cluster"},
    {"skill_id": "rust", "quote": "Rust everywhere"},  # unknown skill
    {"skill_id": "redis", "quote": "cached with Redis"},  # not in the report
]


def _stub_report(monkeypatch, text=REPORT_TEXT, claims=GEMINI_CLAIMS):
    prompts = []
    monkeypatch.setattr(report_parse, "extract_certificate_text", lambda content: text)

    def propose(prompt, key, cache_prefix):
        prompts.append(prompt)
        if isinstance(claims, Exception):
            raise claims
        return claims

    monkeypatch.setattr(report_parse, "propose_json_items", propose)
    return prompts


def test_report_claims_are_validated_deduped_and_repo_links_extracted(monkeypatch) -> None:
    prompts = _stub_report(monkeypatch)
    report = report_parse.parse_report("C:\\Users\\me\\Final Report.pdf", b"%PDF-1.4")
    assert report["filename"] == "Final Report.pdf"
    assert report["claims"] == [
        {"skill_id": "python", "quote": "backend API in Python"},
        {"skill_id": "flask", "quote": "Python with Flask"},
        {"skill_id": "docker", "quote": "run in Docker containers"},
        {"skill_id": "kubernetes", "quote": "on a Kubernetes cluster"},
    ]
    reasons = {(item["skill_id"], item["reason"]) for item in report["dropped"]}
    assert reasons == {
        ("rust", "Not a skill in the skill framework."),
        ("redis", "Quote not found in the text."),
    }
    assert report["repo_links"] == [
        {"owner": "Student-Dev", "repo": "parking-api", "url": "https://github.com/Student-Dev/parking-api"},
        {"owner": "someone-else", "repo": "parking-ui", "url": "https://github.com/someone-else/parking-ui"},
    ]
    assert "error" not in report
    assert "student@example.com" not in prompts[0]  # personal details redacted before the AI call
    assert "text" not in report and "raw_text" not in report  # the report text is not kept


def test_report_claims_are_capped_at_fifteen(monkeypatch) -> None:
    skills = ["python", "java", "javascript", "typescript", "flask", "django", "fastapi", "react",
              "sql", "mysql", "mongodb", "redis", "docker", "kubernetes", "git", "pandas", "aws"]
    text = " ".join(f"used {skill} here." for skill in skills)
    _stub_report(monkeypatch, text, [{"skill_id": skill, "quote": f"used {skill} here"} for skill in skills])
    report = report_parse.parse_report("r.pdf", b"%PDF-1.4")
    assert len(report["claims"]) == 15
    assert [item["skill_id"] for item in report["dropped"]] == ["pandas", "aws"]
    assert all(item["reason"] == "Over the limit of 15." for item in report["dropped"])


def test_ai_unavailable_keeps_the_report_with_a_note(monkeypatch) -> None:
    _stub_report(monkeypatch, claims=GeminiUnavailableError("down"))
    report = report_parse.parse_report("r.pdf", b"%PDF-1.4")
    assert report["claims"] == [] and report["error"] == report_parse.EXTRACTION_FAILED
    assert len(report["repo_links"]) == 2


def test_unreadable_reports_raise_like_certificates() -> None:
    with pytest.raises(ValueError, match="empty"):
        report_parse.parse_report("r.pdf", b"")
    with pytest.raises(ValueError, match="PDF, PNG, or JPEG"):
        report_parse.parse_report("r.txt", b"plain text")


CODE = {"type": "code", "repo": "parking-api", "file_path": "app/routes.py", "start_line": 3, "end_line": 9,
        "url": "https://github.com/Student-Dev/parking-api/blob/abc/app/routes.py#L3-L9"}
TESTED = {**CODE, "file_path": "app/engine.py", "url": "https://github.com/Student-Dev/parking-api/blob/abc/app/engine.py#L1-L5",
          "start_line": 1, "end_line": 5, "demonstrated_by": {"path": "tests/test_engine.py", "match": "name"}}
DEPENDENCY = {"type": "dependency", "repo": "parking-api", "file_path": "requirements.txt", "start_line": 1,
              "end_line": 1, "url": "u", "checks": {"authored": True}}
UNAUTHORED_DOCKERFILE = {"type": "config", "repo": "parking-api", "file_path": "Dockerfile", "start_line": 0,
                         "end_line": 0, "url": "d", "checks": {"authored": False}}


def passport_skills() -> list[dict]:
    return [
        {"skill_id": "python", "name": "Python", "level": "Demonstrated", "certified": False, "evidence": [CODE, TESTED]},
        {"skill_id": "flask", "name": "Flask", "level": "Used", "certified": False, "evidence": [DEPENDENCY]},
        # A Dockerfile the student never committed to: attached, not counted, so still Claimed.
        {"skill_id": "docker", "name": "Docker", "level": "Claimed", "certified": False, "evidence": [UNAUTHORED_DOCKERFILE]},
        {"skill_id": "aws", "name": "AWS", "level": "Claimed", "certified": False, "evidence": [], "resume_mention": "AWS"},
    ]


PARSED = [{
    "filename": "report.pdf",
    "claims": [
        {"skill_id": "python", "quote": "backend API in Python"},
        {"skill_id": "flask", "quote": "Python with Flask"},
        {"skill_id": "docker", "quote": "run in Docker containers"},
        {"skill_id": "kubernetes", "quote": "on a Kubernetes cluster"},
    ],
    "dropped": [],
    "repo_links": [
        {"owner": "STUDENT-dev", "repo": "parking-api", "url": "https://github.com/STUDENT-dev/parking-api"},
        {"owner": "someone-else", "repo": "parking-ui", "url": "https://github.com/someone-else/parking-ui"},
    ],
}]


def test_claims_are_corroborated_only_by_counted_evidence() -> None:
    skills = passport_skills()
    before = copy.deepcopy(skills)
    [report] = report_parse.report_results(PARSED, skills, "student-dev")
    statuses = {claim["skill_id"]: claim for claim in report["claims"]}
    assert statuses["python"]["status"] == "corroborated"
    assert statuses["python"]["evidence"]["file_path"] == "app/engine.py"  # the tested citation is strongest
    assert statuses["flask"]["status"] == "corroborated"
    assert statuses["flask"]["evidence"]["type"] == "dependency"
    assert statuses["docker"]["status"] == "not_evidenced" and "evidence" not in statuses["docker"]
    assert statuses["kubernetes"]["status"] == "not_evidenced"
    assert skills == before  # levels and evidence untouched


def test_repo_links_are_own_or_other_account_case_insensitively() -> None:
    [report] = report_parse.report_results(PARSED, passport_skills(), "Student-Dev")
    assert [(link["repo"], link["status"]) for link in report["repo_links"]] == [
        ("parking-api", "own"), ("parking-ui", "other_account"),
    ]


def test_not_evidenced_claims_become_unverified_mentions_that_never_meet_a_requirement() -> None:
    skills = passport_skills()
    reports = report_parse.report_results(PARSED, skills, "student-dev")
    report_parse.add_report_mentions(skills, reports)
    by_id = {skill["skill_id"]: skill for skill in skills}
    assert by_id["kubernetes"] == {
        "skill_id": "kubernetes", "name": "Kubernetes", "level": "Claimed", "certified": False,
        "evidence": [], "report_mention": "on a Kubernetes cluster",
    }
    assert by_id["docker"]["level"] == "Claimed" and by_id["docker"]["report_mention"] == "run in Docker containers"
    assert "report_mention" not in by_id["python"]  # corroborated claims add nothing
    assert by_id["python"]["level"] == "Demonstrated" and by_id["flask"]["level"] == "Used"
    role = {"id": "custom-k", "name": "Platform Intern", "requirements": [
        {"skill_id": "kubernetes", "importance": "must", "min_level": "Used"},
        {"skill_id": "docker", "importance": "nice", "min_level": "Used"},
    ]}
    statuses = {item["skill_id"]: item["status"] for item in match_role(skills, role)["requirements"]}
    assert statuses == {"kubernetes": "missing", "docker": "missing"}
    assert match_role(skills, "backend-intern")["met"] == match_role(passport_skills(), "backend-intern")["met"]


def test_portfolio_ownership_only_for_the_users_github_pages_and_never_fetched(monkeypatch) -> None:
    def no_network(*_args, **_kwargs):
        raise AssertionError("the portfolio URL must never be fetched")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(httpx.Client, "send", no_network)
    assert report_parse.portfolio_status("https://Student-Dev.GitHub.io/projects", "student-dev") == {
        "url": "https://Student-Dev.GitHub.io/projects", "status": "own_github_pages",
    }
    for url in ("https://student-dev.github.io.evil.com", "https://other.github.io", "https://student-dev.dev",
                "https://www.student-dev.github.io"):
        assert report_parse.portfolio_status(url, "student-dev")["status"] == "external_unverified", url


def test_portfolio_url_validation() -> None:
    assert report_parse.validate_portfolio_url("  ") is None
    assert report_parse.validate_portfolio_url(" https://me.github.io ") == "https://me.github.io"
    for bad in ("http://me.github.io", "me.github.io", "https://", "https://a b.com", "javascript:alert(1)",
                "https://user:pass@me.github.io", "https://x.io/" + "a" * 300):
        with pytest.raises(ValueError):
            report_parse.validate_portfolio_url(bad)


def _stub_pipeline(monkeypatch, existing, skills):
    saved: list[dict] = []
    monkeypatch.setattr(pipeline, "get_passport", lambda _: existing)
    monkeypatch.setattr(pipeline, "ingest_github", lambda username, force_refresh=False, target_skills=None: {"username": "Student-Dev", "repo_count": 1, "repos": []})
    monkeypatch.setattr(pipeline, "propose_claims", lambda *_: [])
    monkeypatch.setattr(pipeline, "verify_claims", lambda *_a, **_k: {"skills": skills, "rejected_claims": []})
    monkeypatch.setattr(pipeline, "add_copy_signals", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "exclude_uncounted_citations", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "generate_growth_and_kit", lambda *_: {"growth_tasks": [], "interview_kit": []})
    monkeypatch.setattr(pipeline, "save_passport", saved.append)
    return saved


def test_pipeline_adds_reports_and_portfolio_after_levels_are_final(monkeypatch) -> None:
    _stub_pipeline(monkeypatch, None, passport_skills())
    monkeypatch.setattr(pipeline, "parse_report", lambda name, content: {**PARSED[0], "filename": name})
    passport = pipeline.build_passport(
        github_username="student-dev", target_role="backend-intern", resume_bytes=b"",
        resume_skill_mentions=[], is_demo=True,
        report_files=[("report.pdf", b"%PDF-1.4")], portfolio_url="https://student-dev.github.io",
    )
    levels = {skill["skill_id"]: skill["level"] for skill in passport["skills"]}
    assert levels == {"python": "Demonstrated", "flask": "Used", "docker": "Claimed", "aws": "Claimed", "kubernetes": "Claimed"}
    assert passport["sources"]["reports"] == 1
    assert [claim["status"] for claim in passport["reports"][0]["claims"]] == [
        "corroborated", "corroborated", "not_evidenced", "not_evidenced",
    ]
    assert passport["portfolio"] == {"url": "https://student-dev.github.io", "status": "own_github_pages"}
    docker = next(item for item in passport["role_match"]["requirements"] if item["skill_id"] == "docker")
    assert docker["status"] == "missing"


def test_passports_without_reports_are_unchanged(monkeypatch) -> None:
    _stub_pipeline(monkeypatch, None, passport_skills())
    passport = pipeline.build_passport(
        github_username="student-dev", target_role="backend-intern", resume_bytes=b"",
        resume_skill_mentions=[], is_demo=True,
    )
    assert "reports" not in passport and "portfolio" not in passport and "reports" not in passport["sources"]


def test_recheck_recomputes_report_statuses_and_keeps_the_portfolio(monkeypatch) -> None:
    _stub_pipeline(monkeypatch, None, passport_skills())
    monkeypatch.setattr(pipeline, "parse_report", lambda name, content: {**PARSED[0], "filename": name})
    first = pipeline.build_passport(
        github_username="student-dev", target_role="backend-intern", resume_bytes=b"",
        resume_skill_mentions=[], is_demo=True,
        report_files=[("report.pdf", b"%PDF-1.4")], portfolio_url="https://example.com/me",
    )
    # After a growth task the student now commits a Dockerfile: Docker becomes Used.
    authored_dockerfile = {**UNAUTHORED_DOCKERFILE, "checks": {"authored": True}}
    new_skills = passport_skills()
    new_skills[2] = {**new_skills[2], "level": "Used", "evidence": [authored_dockerfile]}
    _stub_pipeline(monkeypatch, first, new_skills)
    monkeypatch.setattr(pipeline, "parse_report", lambda *_: (_ for _ in ()).throw(AssertionError("reports aren't re-read")))
    captured = {}
    real_build = pipeline.build_passport
    monkeypatch.setattr(pipeline, "build_passport", lambda **kwargs: captured.update(kwargs) or real_build(**kwargs))
    rechecked = pipeline.recheck_passport(first)
    assert "report_files" not in captured
    claims = {claim["skill_id"]: claim for claim in rechecked["reports"][0]["claims"]}
    assert claims["docker"]["status"] == "corroborated"
    assert claims["docker"]["quote"] == "run in Docker containers"
    assert claims["kubernetes"]["status"] == "not_evidenced"
    assert rechecked["portfolio"] == {"url": "https://example.com/me", "status": "external_unverified"}
    assert {"skill_id": "docker", "from": "Claimed", "to": "Used"} in rechecked["level_changes"]


def test_student_view_hides_the_other_account_warning_and_report_mentions() -> None:
    skills = passport_skills()
    reports = report_parse.report_results(PARSED, skills, "student-dev")
    report_parse.add_report_mentions(skills, reports)
    passport = {"id": "p", "skills": skills, "reports": reports, "rejected_claims": [], "interview_kit": [],
                "portfolio": {"url": "https://example.com", "status": "external_unverified"}}
    view = main_module.student_view(passport)
    assert [link["status"] for link in view["reports"][0]["repo_links"]] == ["own"]
    assert "other_account" not in repr(view)
    assert [claim["status"] for claim in view["reports"][0]["claims"]] == [
        "corroborated", "corroborated", "not_evidenced", "not_evidenced",
    ]
    assert all("report_mention" not in skill for skill in view["skills"])
    assert [link["status"] for link in passport["reports"][0]["repo_links"]] == ["own", "other_account"]  # stored data intact


def test_reading_reports_errors_are_specific(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "get_job", lambda _job_id: {"step": "Reading reports"})
    assert "project report is empty" in pipeline._public_job_error("job", ValueError("Certificate file is empty."))
    assert "couldn’t read text" in pipeline._public_job_error("job", ValueError("No readable text found in the certificate PDF."))


PDF = ("report.pdf", b"%PDF-1.4 report", "application/pdf")


def _create(client, files=(), **data):
    return client.post(
        "/passport/create",
        data={"github_username": "student-dev", "target_role": "backend-intern", "is_demo": "true", **data},
        files=[("resume", ("resume.pdf", b"%PDF-1.4 resume", "application/pdf")), *files],
    )


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(config, "ENABLE_REPORTS", True)
    received: list[dict] = []
    monkeypatch.setattr(main_module, "run_passport_job", lambda *args, **kwargs: received.append(kwargs))
    return TestClient(main_module.app), received


def test_create_accepts_reports_and_a_portfolio_url(api) -> None:
    client, received = api
    png = ("scan.png", b"\x89PNG\r\n\x1a\n image", "image/png")
    response = _create(client, [("reports", PDF), ("reports", png)], portfolio_url="https://student-dev.github.io")
    assert response.status_code == 202
    assert received[0]["report_files"] == [("report.pdf", PDF[1]), ("scan.png", png[1])]
    assert received[0]["portfolio_url"] == "https://student-dev.github.io"


def test_create_rejects_bad_reports_and_urls(api) -> None:
    client, received = api
    too_many = _create(client, [("reports", PDF)] * 4)
    assert too_many.status_code == 422 and "At most 3" in too_many.json()["detail"]
    wrong_type = _create(client, [("reports", ("notes.txt", b"plain text", "text/plain"))])
    assert wrong_type.status_code == 422 and "PDF, PNG, or JPEG" in wrong_type.json()["detail"]
    for url in ("http://student-dev.github.io", "https://x.io/" + "a" * 300, "ftp://files"):
        response = _create(client, portfolio_url=url)
        assert response.status_code == 422 and "Portfolio URL" in response.json()["detail"], url
    assert received == []


def test_reports_count_towards_the_upload_cap(api) -> None:
    client, _ = api
    big = b"%PDF-1.4 " + b"r" * (8 * 1024 * 1024 + 512 * 1024)  # 3 x 8.5 MB: over 25 MB, under the 26 MB body cap
    response = _create(client, [("certificates", ("c.pdf", big, "application/pdf")),
                                ("reports", ("r1.pdf", big, "application/pdf")),
                                ("reports", ("r2.pdf", big, "application/pdf"))])
    assert response.status_code == 413
    assert "resume, certificates and reports" in response.json()["detail"]


def test_flag_off_ignores_report_inputs(api, monkeypatch) -> None:
    client, received = api
    monkeypatch.setattr(config, "ENABLE_REPORTS", False)
    assert client.get("/features").json()["reports"] is False
    response = _create(client, [("reports", PDF)] * 4, portfolio_url="http://not-checked")
    assert response.status_code == 202
    assert received[0]["report_files"] == [] and received[0]["portfolio_url"] is None
