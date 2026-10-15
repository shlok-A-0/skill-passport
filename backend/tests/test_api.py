import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.main as main_module
from app.services import storage
from app.services.jobs import complete_job


def fake_passport(passport_id: str) -> dict:
    return {
        "id": passport_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "github_username": "demo-user",
        "target_role": "Backend Developer Intern",
        "sources": {"repos": 1, "resume": True, "certificates": 0},
        "skills": [
            {
                "skill_id": "python",
                "level": "Applied",
                "certified": False,
                "evidence": [
                    {
                        "type": "code",
                        "repo": "demo",
                        "file_path": "main.py",
                        "start_line": 1,
                        "end_line": 2,
                        "commit_sha": "abc123",
                        "url": "https://github.com/demo/demo/blob/abc123/main.py#L1-L2",
                        "checks": {
                            "file_exists": True,
                            "lines_valid": True,
                            "authored": True,
                            "not_fork": True,
                            "signature_found": True,
                        },
                    }
                ],
            },
            {
                "skill_id": "aws",
                "level": "Claimed",
                "certified": False,
                "evidence": [],
            },
        ],
        "rejected_claims": [],
        "role_match": {"role_id": "backend-intern", "met": 1, "total": 2, "requirements": []},
        "growth_tasks": [],
        "interview_kit": [],
        "is_demo": True,
        "resume_skill_mentions": [{"skill_id": "aws", "mention": "AWS"}],
        "certificate_skill_mentions": [],
    }


def test_create_job_persist_and_get_passport(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "test.sqlite")

    def fake_runner(job_id, username, role, resume, certificates, is_demo):
        passport = fake_passport(job_id)
        passport["github_username"] = username
        passport["is_demo"] = is_demo
        storage.save_passport(passport)
        complete_job(job_id, passport["id"])

    monkeypatch.setattr(main_module, "run_passport_job", fake_runner)
    client = TestClient(main_module.app)
    response = client.post(
        "/passport/create",
        data={
            "github_username": "demo-user",
            "target_role": "backend-intern",
            "is_demo": "true",
        },
        files={"resume": ("demo.pdf", b"%PDF-1.4 demo", "application/pdf")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert client.get(f"/jobs/{job_id}").json()["status"] == "done"
    passport = client.get(f"/passport/{job_id}")
    assert passport.status_code == 200
    assert passport.json()["is_demo"] is True
    assert client.get("/passports").json()[0]["id"] == job_id


def test_create_rejects_non_pdf_resume() -> None:
    client = TestClient(main_module.app)
    response = client.post(
        "/passport/create",
        data={"github_username": "demo-user", "target_role": "backend-intern"},
        files={"resume": ("resume.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 415


def passport_with_fraud_signals(passport_id: str) -> dict:
    passport = fake_passport(passport_id)
    passport["skills"][0]["evidence"][0]["integrity"] = [
        {"id": "commit_spread", "label": "Work spread over time", "status": "warn", "detail": "All on one day."},
    ]
    passport["interview_kit"] = [{"skill_id": "python", "questions": [
        {"q": "Why this loop?", "good_answer_mentions": "the batching in lines 3-9"},
    ]}]
    passport["rejected_claims"] = [
        {"claim": {"skill_id": "flask", "repo": "demo", "file_path": "x.py"}, "failed_checks": ["lines_valid"],
         "reason": "Failed verification checks: lines_valid.", "kind": "unsupported"},
        {"claim": {"skill_id": "python", "repo": "demo", "file_path": "copied.py"}, "failed_checks": ["not_copied"],
         "reason": "Same code found in someone/original, created 2020-01-01, before this repository (created 2024-01-01).",
         "source_url": "https://github.com/someone/original/blob/main/a.py", "kind": "copied"},
        {"claim": {"skill_id": "python", "repo": "demo", "file_path": "copied2.py"}, "failed_checks": ["not_copied"],
         "reason": "Same code found in someone/original.", "source_url": "https://github.com/someone/original",
         "kind": "copied"},
    ]
    return passport


def _keys_and_values(data: object) -> tuple[set, list]:
    keys: set = set()
    values: list = []
    if isinstance(data, dict):
        for key, value in data.items():
            keys.add(key)
            values.append(value)
            more_keys, more_values = _keys_and_values(value)
            keys |= more_keys
            values += more_values
    elif isinstance(data, list):
        for value in data:
            more_keys, more_values = _keys_and_values(value)
            keys |= more_keys
            values += more_values
    return keys, values


def _review_client(tmp_path, monkeypatch, recruiter_key: str = "") -> TestClient:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(main_module, "RECRUITER_KEY", recruiter_key)
    storage.save_passport(passport_with_fraud_signals("p-review"))
    return TestClient(main_module.app)


def test_student_endpoints_hide_fraud_signals(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    for body in (client.get("/passport/p-review").json(), client.get("/passports").json()):
        keys, values = _keys_and_values(body)
        assert not {"integrity", "good_answer_mentions", "source_url"} & keys
        assert "copied" not in values
        assert not any("someone/original" in str(value) for value in values)
    rejected = client.get("/passport/p-review").json()["rejected_claims"]
    assert [item["kind"] for item in rejected] == ["unsupported", "review"]
    assert rejected[1] == {"kind": "review", "count": 2, "reason": "2 citations were not counted after authorship review."}


def test_new_and_legacy_earlier_source_kinds_are_both_handled(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    passport = passport_with_fraud_signals("p-kinds")
    passport["rejected_claims"][2]["kind"] = "earlier_source"  # one new-style, one legacy "copied"
    storage.save_passport(passport)
    rejected = client.get("/passport/p-kinds").json()["rejected_claims"]
    assert [item["kind"] for item in rejected] == ["unsupported", "review"]
    assert rejected[1]["count"] == 2
    review = client.get("/passport/p-kinds/review").json()["rejected_claims"]
    assert {item["kind"] for item in review} == {"unsupported", "copied", "earlier_source"}


def test_review_endpoints_return_full_passport(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    for body in (client.get("/passport/p-review/review").json(), client.get("/passports/review").json()):
        keys, values = _keys_and_values(body)
        assert {"integrity", "good_answer_mentions", "source_url"} <= keys
        assert "copied" in values


def test_review_endpoints_require_matching_recruiter_key_when_set(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch, recruiter_key="s3cret")
    for path in ("/passport/p-review/review", "/passports/review"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-Recruiter-Key": "wrong"}).status_code == 401
        assert client.get(path, headers={"X-Recruiter-Key": "s3cret"}).status_code == 200
    # The student view stays open.
    assert client.get("/passport/p-review").status_code == 200


def test_recheck_returns_student_view(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main_module, "recheck_passport",
        lambda existing: {**passport_with_fraud_signals(existing["id"]), "level_changes": []},
    )
    keys, values = _keys_and_values(client.post("/passport/p-review/recheck").json())
    assert not {"integrity", "good_answer_mentions", "source_url"} & keys
    assert "copied" not in values


def test_api_returns_skill_names(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    roles = client.get("/roles").json()
    backend = next(role for role in roles if role["id"] == "backend-intern")
    assert all(item["name"] for item in backend["requirements"])
    passport = passport_with_fraud_signals("p-names")
    passport["skills"][0]["name"] = "Python"
    storage.save_passport(passport)
    for path in ("/passport/p-names", "/passport/p-names/review"):
        assert client.get(path).json()["skills"][0]["name"] == "Python"


def test_coverage_is_in_student_and_review_responses(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    passport = passport_with_fraud_signals("p-coverage")
    passport["coverage"] = {"repos_analysed": 2, "source_files_read": 9, "source_files_total": 40,
                            "lines_read": 1200, "repos_skipped": [{"name": "old", "reason": "fork"}]}
    storage.save_passport(passport)
    for path in ("/passport/p-coverage", "/passport/p-coverage/review"):
        assert client.get(path).json()["coverage"] == passport["coverage"]


def test_badge_contains_verified_achievement_only_and_is_unsigned() -> None:
    from app.services.badge import create_badge

    badge = create_badge(fake_passport("passport-1"))
    subject = badge["credentialSubject"]
    assert [item["name"] for item in subject["achievement"]] == ["python"]
    assert badge["prototypeNote"] == "unsigned prototype"
    assert "proof" not in badge
    assert "credentialStatus" not in badge
