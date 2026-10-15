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


def test_badge_contains_verified_achievement_only_and_is_unsigned() -> None:
    from app.services.badge import create_badge

    badge = create_badge(fake_passport("passport-1"))
    subject = badge["credentialSubject"]
    assert [item["name"] for item in subject["achievement"]] == ["python"]
    assert badge["prototypeNote"] == "unsigned prototype"
    assert "proof" not in badge
    assert "credentialStatus" not in badge
