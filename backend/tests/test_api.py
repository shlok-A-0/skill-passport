import hashlib
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
    access_token = response.json()["access_token"]
    assert access_token
    assert client.get(f"/jobs/{job_id}").status_code == 404
    job_response = client.get(f"/jobs/{job_id}", headers={"X-Passport-Token": access_token})
    assert job_response.json()["status"] == "done"
    assert "access_token_hash" not in job_response.json()
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
        # Legacy stored format ("copied"), as written before the earlier-source rename.
        {"claim": {"skill_id": "python", "repo": "demo", "file_path": "legacy.py", "start_line": 1, "end_line": 9},
         "failed_checks": ["not_copied"],
         "reason": "Same code found in someone/original, created 2020-01-01, before this repository (created 2024-01-01).",
         "source_url": "https://github.com/someone/original/blob/main/a.py", "kind": "copied"},
        {"claim": {"skill_id": "python", "repo": "demo", "file_path": "helpers.py", "start_line": 3, "end_line": 7},
         "failed_checks": ["earlier_source"],
         "reason": "Matches code that appeared in someone/original on 2020-01-01, before this file's first commit here.",
         "url": "https://github.com/demo-user/demo/blob/abc123/helpers.py#L3-L7",
         "source_repo": "someone/original", "source_url": "https://github.com/someone/original",
         "source_first_seen": "2020-01-01T00:00:00Z", "candidate_first_seen": "2024-01-01T00:00:00Z",
         "kind": "earlier_source"},
    ]
    return passport


EXCLUDED_KEY = "python:demo:helpers.py:3-7"


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


STUDENT_WORDS_NOT_ALLOWED = ("fraud", "fake", "copied", "cheat")
REVIEW_OWNER_TOKEN = "review-owner-session-secret"


def _review_client(tmp_path, monkeypatch, recruiter_key: str = "") -> TestClient:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(main_module, "RECRUITER_KEY", recruiter_key)
    storage.save_passport(passport_with_fraud_signals("p-review"))
    storage.create_stored_job(
        "p-review",
        target="demo-user",
        access_token_hash=hashlib.sha256(REVIEW_OWNER_TOKEN.encode()).hexdigest(),
    )
    storage.complete_stored_job("p-review", "p-review", [])
    return TestClient(main_module.app, headers={"X-Passport-Token": REVIEW_OWNER_TOKEN})
def test_student_view_lists_excluded_citations_without_advisory_signals(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    for body in (client.get("/passport/p-review").json(), client.get("/passports").json()):
        keys, values = _keys_and_values(body)
        # Detector internals stay hidden...
        assert not {"integrity", "good_answer_mentions", "source_first_seen", "candidate_first_seen",
                    "source_repo"} & keys
        assert not any(isinstance(value, str) and word in value.lower()
                       for value in values for word in STUDENT_WORDS_NOT_ALLOWED)
    rejected = client.get("/passport/p-review").json()["rejected_claims"]
    # ...but every outcome is shown: one item per excluded citation (legacy and new kinds alike).
    assert [item["kind"] for item in rejected] == ["unsupported", "earlier_source", "earlier_source"]
    excluded = rejected[2]
    assert excluded == {
        "kind": "earlier_source",
        "citation_key": EXCLUDED_KEY,
        "claim": {"skill_id": "python", "repo": "demo", "file_path": "helpers.py", "start_line": 3, "end_line": 7},
        "url": "https://github.com/demo-user/demo/blob/abc123/helpers.py#L3-L7",
        "source_url": "https://github.com/someone/original",
        "reason": main_module.STUDENT_EXCLUDED_REASON,
        "review_request": None,
    }
    assert rejected[1]["citation_key"] == "python:demo:legacy.py:1-9"


def test_student_view_hides_stored_resume_and_certificate_mentions(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    passport = passport_with_fraud_signals("p-private-mentions")
    marker = "PRIVATE-RESUME-MARKER"
    passport["resume_skill_mentions"] = [{"skill_id": "python", "mention": marker}]
    passport["certificate_skill_mentions"] = [{"skill_id": "aws", "mention": marker}]
    passport["skills"][0]["resume_mention"] = marker
    storage.save_passport(passport)

    response = client.get("/passport/p-private-mentions")
    assert response.status_code == 200
    assert marker not in response.text
    assert "resume_skill_mentions" not in response.json()
    assert "certificate_skill_mentions" not in response.json()
    assert "resume_mention" not in response.json()["skills"][0]
    stored = storage.get_passport("p-private-mentions")
    assert stored["resume_skill_mentions"][0]["mention"] == marker
    assert stored["certificate_skill_mentions"][0]["mention"] == marker


def test_new_and_legacy_earlier_source_kinds_are_both_handled(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    review = client.get("/passport/p-review/review").json()["rejected_claims"]
    assert {item["kind"] for item in review} == {"unsupported", "copied", "earlier_source"}
    # Both kinds can be the subject of a review request.
    for key in ("python:demo:legacy.py:1-9", EXCLUDED_KEY):
        assert client.post("/passport/p-review/review-requests", json={"citation_key": key}).status_code == 201


def test_review_request_requires_owner_token(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    path = "/passport/p-review/review-requests"
    body = {"citation_key": EXCLUDED_KEY}
    assert client.post(path, json=body, headers={"X-Passport-Token": ""}).status_code == 404
    assert client.post(path, json=body, headers={"X-Passport-Token": "not-the-owner"}).status_code == 404
    response = client.post(path, json=body, headers={"X-Passport-Token": REVIEW_OWNER_TOKEN})
    assert response.status_code == 201


def test_review_request_on_a_counted_or_unsupported_citation_is_rejected(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    for key in ("python:demo:main.py:1-2", "flask:demo:x.py:None-None", "nonsense"):
        response = client.post("/passport/p-review/review-requests", json={"citation_key": key, "note": "mine"})
        assert response.status_code == 422
    assert client.post("/passport/missing/review-requests", json={"citation_key": EXCLUDED_KEY}).status_code == 404
    too_long = client.post("/passport/p-review/review-requests", json={"citation_key": EXCLUDED_KEY, "note": "x" * 1001})
    assert too_long.status_code == 422


def test_review_request_limits(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    first = client.post("/passport/p-review/review-requests",
                        json={"citation_key": EXCLUDED_KEY, "note": "This is from my old account."})
    assert first.status_code == 201 and first.json()["status"] == "open"
    # One open request per citation.
    assert client.post("/passport/p-review/review-requests", json={"citation_key": EXCLUDED_KEY}).status_code == 409
    # At most 10 per passport.
    passport = passport_with_fraud_signals("p-many")
    template = passport["rejected_claims"][2]
    passport["rejected_claims"] = [
        {**template, "claim": {**template["claim"], "start_line": index, "end_line": index + 1}}
        for index in range(1, 13)
    ]
    storage.save_passport(passport)
    storage.create_stored_job(
        "p-many",
        access_token_hash=hashlib.sha256(REVIEW_OWNER_TOKEN.encode()).hexdigest(),
    )
    storage.complete_stored_job("p-many", "p-many", [])
    codes = [
        client.post("/passport/p-many/review-requests",
                    json={"citation_key": f"python:demo:helpers.py:{index}-{index + 1}"}).status_code
        for index in range(1, 12)
    ]
    assert codes == [201] * 10 + [429]


def test_concurrent_duplicate_review_requests_return_conflict(tmp_path, monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor

    client = _review_client(tmp_path, monkeypatch)
    path = "/passport/p-review/review-requests"
    body = {"citation_key": EXCLUDED_KEY}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(path, json=body), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]


def test_review_requests_survive_recheck_and_never_change_levels(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    before = client.get("/passport/p-review/review").json()
    client.post("/passport/p-review/review-requests", json={"citation_key": EXCLUDED_KEY, "note": "Mine."})
    after_request = client.get("/passport/p-review/review").json()
    assert after_request["skills"] == before["skills"]
    assert after_request["rejected_claims"] == before["rejected_claims"]

    def rebuild(existing):
        result = {**passport_with_fraud_signals(existing["id"]), "level_changes": []}
        storage.save_passport(result)
        return result

    def rebuild_job(job_id, existing):
        rebuild(existing)
        storage.complete_stored_job(job_id, existing["id"], [])

    monkeypatch.setattr(main_module, "run_recheck_job", rebuild_job)
    started = client.post(
        "/passport/p-review/recheck",
        headers={"X-Passport-Token": REVIEW_OWNER_TOKEN},
    )
    assert started.status_code == 202
    job_id = started.json()["job_id"]
    job = client.get(f"/jobs/{job_id}", headers={"X-Passport-Token": started.json()["access_token"]})
    assert job.json()["status"] == "done"
    student = client.get("/passport/p-review").json()
    excluded = next(item for item in student["rejected_claims"] if item.get("citation_key") == EXCLUDED_KEY)
    assert excluded["review_request"]["status"] == "open"
    requests = client.get("/passport/p-review/review").json()["review_requests"]
    assert [(item["citation_key"], item["note"], item["status"]) for item in requests] == [(EXCLUDED_KEY, "Mine.", "open")]


def test_mark_reviewed_requires_the_key_when_set(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch, recruiter_key="s3cret")
    request_id = client.post("/passport/p-review/review-requests", json={"citation_key": EXCLUDED_KEY}).json()["id"]
    path = f"/review-requests/{request_id}/mark-reviewed"
    assert client.post(path).status_code == 401
    assert client.post(path, headers={"X-Recruiter-Key": "wrong"}).status_code == 401
    reviewed = client.post(path, headers={"X-Recruiter-Key": "s3cret"})
    assert reviewed.status_code == 200 and reviewed.json()["status"] == "reviewed" and reviewed.json()["reviewed_at"]
    assert client.post("/review-requests/nope/mark-reviewed", headers={"X-Recruiter-Key": "s3cret"}).status_code == 404
    # After review, the student may ask again.
    assert client.post("/passport/p-review/review-requests", json={"citation_key": EXCLUDED_KEY}).status_code == 201


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


def test_passports_list_enforces_recruiter_key_when_set(tmp_path, monkeypatch) -> None:
    # Even outside production, a configured RECRUITER_KEY must protect the full list route.
    client = _review_client(tmp_path, monkeypatch, recruiter_key="s3cret")
    assert client.get("/passports").status_code == 401
    assert client.get("/passports", headers={"X-Recruiter-Key": "wrong"}).status_code == 401
    listing = client.get("/passports", headers={"X-Recruiter-Key": "s3cret"})
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == "p-review"


def test_review_endpoints_fail_closed_in_production_mode(tmp_path, monkeypatch) -> None:
    # In production without RECRUITER_KEY configured, fail-closed with 500
    monkeypatch.setattr(main_module, "REQUIRE_RECRUITER_AUTH", True)
    client_unconfigured = _review_client(tmp_path, monkeypatch, recruiter_key="")
    assert client_unconfigured.get("/passport/p-review/review").status_code == 500

    # In production with RECRUITER_KEY configured, unauthenticated requests get 401
    client_configured = _review_client(tmp_path, monkeypatch, recruiter_key="prod-secret")
    assert client_configured.get("/passport/p-review/review").status_code == 401
    assert client_configured.get("/passport/p-review/review", headers={"X-Recruiter-Key": "prod-secret"}).status_code == 200


def test_application_startup_requires_recruiter_key_when_auth_is_required(monkeypatch) -> None:
    import pytest

    monkeypatch.setattr(main_module, "REQUIRE_RECRUITER_AUTH", True)
    monkeypatch.setattr(main_module, "RECRUITER_KEY", "")
    with pytest.raises(RuntimeError, match="RECRUITER_KEY must be configured"):
        with TestClient(main_module.app):
            pass


def test_commit_signature_integration_in_recruiter_view(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    passport_data = passport_with_fraud_signals("p-sig")
    python_skill = next(s for s in passport_data["skills"] if s["skill_id"] == "python")
    code_evidence = python_skill["evidence"][0]
    code_evidence["integrity"] = [
        {
            "id": "commit_signature",
            "label": "Cryptographic commit signature",
            "status": "ok",
            "detail": "Commit is cryptographically signed (verified GPG/SSH key).",
        }
    ]
    storage.save_passport(passport_data)
    review = client.get("/passport/p-sig/review").json()
    signals = review["skills"][0]["evidence"][0]["integrity"]
    sig_signal = next(s for s in signals if s["id"] == "commit_signature")
    assert sig_signal["status"] == "ok"
    assert "verified GPG/SSH key" in sig_signal["detail"]


def test_api_concurrency_and_active_job_limits(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "test.sqlite")
    # Stub run_passport_job so the background task doesn't actually run
    monkeypatch.setattr(main_module, "run_passport_job", lambda *args, **kwargs: None)
    client = TestClient(main_module.app)
    resume_file = ("resume.pdf", b"%PDF-1.4 test", "application/pdf")

    # 1. Active duplicate username guard
    storage.create_stored_job("job-1", target="active-user")
    storage.update_stored_job("job-1", "Reading GitHub", [])
    res = client.post(
        "/passport/create",
        data={"github_username": "active-user", "target_role": "backend-intern"},
        files={"resume": resume_file},
    )
    assert res.status_code == 409
    assert "already in progress" in res.json()["detail"]

    # 2. Concurrency limit guard (5 jobs)
    for i in range(2, 6):
        storage.create_stored_job(f"job-{i}", target=f"user-{i}")
        storage.update_stored_job(f"job-{i}", "Reading GitHub", [])
    res_max = client.post(
        "/passport/create",
        data={"github_username": "new-user", "target_role": "backend-intern"},
        files={"resume": ("resume.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    assert res_max.status_code == 429
    assert "maximum concurrent verification jobs" in res_max.json()["detail"]




def test_recheck_returns_student_view(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    def rebuild_job(job_id, existing):
        storage.save_passport({**passport_with_fraud_signals(existing["id"]), "level_changes": []})
        storage.complete_stored_job(job_id, existing["id"], [])

    monkeypatch.setattr(main_module, "run_recheck_job", rebuild_job)
    started = client.post(
        "/passport/p-review/recheck",
        headers={"X-Passport-Token": REVIEW_OWNER_TOKEN},
    )
    assert started.status_code == 202
    job = client.get(
        f"/jobs/{started.json()['job_id']}",
        headers={"X-Passport-Token": started.json()["access_token"]},
    )
    assert job.json()["status"] == "done"
    keys, values = _keys_and_values(client.get("/passport/p-review").json())
    assert not {"integrity", "good_answer_mentions"} & keys
    assert "copied" not in values


def test_recheck_endpoint_uses_the_background_pipeline_worker(tmp_path, monkeypatch) -> None:
    from app.services import pipeline

    assert main_module.run_recheck_job is pipeline.run_recheck_job
    client = _review_client(tmp_path, monkeypatch)
    calls = []
    def fake_build(**kwargs):
        calls.append(kwargs)
        result = {**passport_with_fraud_signals("p-review"), "level_changes": []}
        storage.save_passport(result)
        complete_job(kwargs["job_id"], "p-review")
        return result

    monkeypatch.setattr(pipeline, "build_passport", fake_build)
    response = client.post(
        "/passport/p-review/recheck",
        headers={"X-Passport-Token": REVIEW_OWNER_TOKEN},
    )
    assert response.status_code == 202
    assert calls[0]["passport_id"] == "p-review" and calls[0]["force_refresh"] is True
    assert calls[0]["job_id"] == response.json()["job_id"]
    assert client.get(
        f"/jobs/{response.json()['job_id']}",
        headers={"X-Passport-Token": response.json()["access_token"]},
    ).json()["status"] == "done"
    assert storage.get_passport_owner_token_hash("p-review") == hashlib.sha256(
        REVIEW_OWNER_TOKEN.encode()
    ).hexdigest()


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


def test_evidence_record_is_in_review_and_badge_but_not_student_output(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    passport = passport_with_fraud_signals("p-record")
    record = {"commit_sha": "abc123", "path": "main.py", "start_line": 1, "end_line": 2,
              "snippet_sha256": "0" * 64, "retrieved_at": "2026-10-15T09:00:00+00:00",
              "verifier_version": "2026-10-15", "checks": {"file_exists": True},
              "rules_applied": ["applied:code_citation"]}
    passport["skills"][0]["evidence"][0]["record"] = record
    storage.save_passport(passport)
    assert client.get("/passport/p-record/review").json()["skills"][0]["evidence"][0]["record"] == record
    keys, _ = _keys_and_values(client.get("/passport/p-record").json())
    assert "record" not in keys and "snippet_sha256" not in keys
    badge = client.get("/passport/p-record/badge").json()
    assert badge["credentialSubject"]["achievement"][0]["evidence"][0]["evidenceRecord"] == record


def test_requirement_packets_are_in_review_responses_only(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    passport = passport_with_fraud_signals("p-packets")
    passport["role_match"]["requirements"] = [
        {"skill_id": "python", "status": "met", "min_level": "Applied", "student_level": "Applied", "importance": "must"},
    ]
    storage.save_passport(passport)
    packets = client.get("/passport/p-packets/review").json()["requirement_packets"]
    assert [(packet["skill_id"], packet["status_label"]) for packet in packets] == [("python", "Met")]
    assert "requirement_packets" not in client.get("/passport/p-packets").json()


def test_coverage_is_in_student_and_review_responses(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    passport = passport_with_fraud_signals("p-coverage")
    passport["coverage"] = {"repos_analysed": 2, "source_files_read": 9, "source_files_total": 40,
                            "lines_read": 1200, "repos_skipped": [{"name": "old", "reason": "fork"}]}
    storage.save_passport(passport)
    for path in ("/passport/p-coverage", "/passport/p-coverage/review"):
        assert client.get(path).json()["coverage"] == passport["coverage"]


def test_check_labels_use_the_new_wording(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    for path in ("/passport/p-review", "/passport/p-review/review"):
        labels = client.get(path).json()["check_labels"]
        assert labels["authored"] == "You committed to this file"
        assert labels["lines_written"] == "Cited lines written by you"
        assert "Authored by you" not in labels.values()


def test_not_written_citation_is_listed_for_the_student_with_a_neutral_reason(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    passport = fake_passport("p-unwritten")
    passport["rejected_claims"] = [{
        "claim": {"skill_id": "python", "repo": "demo", "file_path": "engine.py", "start_line": 2, "end_line": 12},
        "failed_checks": ["not_written_by_candidate"],
        "reason": "None of the cited lines were last changed in your commits.",
        "url": "https://github.com/demo-user/demo/blob/abc123/engine.py#L2-L12",
        "lines_written": {"written": 0, "cited": 11},
        "kind": "not_written_by_candidate",
    }]
    storage.save_passport(passport)
    item = client.get("/passport/p-unwritten").json()["rejected_claims"][0]
    assert item == {
        "kind": "not_written_by_candidate",
        "citation_key": "python:demo:engine.py:2-12",
        "claim": {"skill_id": "python", "repo": "demo", "file_path": "engine.py", "start_line": 2, "end_line": 12},
        "url": "https://github.com/demo-user/demo/blob/abc123/engine.py#L2-L12",
        "source_url": None,
        "reason": "None of the cited lines were last changed in your commits.",
        "review_request": None,
    }
    storage.create_stored_job(
        "p-unwritten",
        access_token_hash=hashlib.sha256(REVIEW_OWNER_TOKEN.encode()).hexdigest(),
    )
    storage.complete_stored_job("p-unwritten", "p-unwritten", [])
    response = client.post("/passport/p-unwritten/review-requests", json={"citation_key": "python:demo:engine.py:2-12"})
    assert response.status_code == 201


def test_badge_uses_readable_skill_names() -> None:
    from app.services.badge import create_badge

    passport = fake_passport("p")
    passport["skills"] = [
        {"skill_id": skill_id, "level": "Used", "certified": False,
         "evidence": [{"type": "config", "url": "u", "checks": {"authored": True}}]}
        for skill_id in ("git", "unit-testing", "rest-api")
    ]
    passport["skills"][0]["name"] = "Git"  # stored name, or looked up for older passports
    achievements = create_badge(passport)["credentialSubject"]["achievement"]
    assert [(item["name"], item["skillId"]) for item in achievements] == [
        ("Git", "git"), ("Unit Testing", "unit-testing"), ("REST API design", "rest-api"),
    ]
    assert all(item["evidence"][0]["narrative"].startswith(f"config evidence for {item['name']};") for item in achievements)


BANNED_WORDS = ("prove", "proves", "proven", "expert", "mastery", "competence", "fraud", "fake", "copied", "cheat")


def test_level_descriptions_make_no_ability_claims() -> None:
    import re

    from app.services.badge import create_badge
    from app.services.verifier import LEVEL_DESCRIPTIONS, LEVEL_NOTE

    texts = [*LEVEL_DESCRIPTIONS.values(), LEVEL_NOTE]
    badge = create_badge(fake_passport("p"))
    texts += [item["description"] for item in badge["credentialSubject"]["achievement"]]
    for text in texts:
        words = set(re.findall(r"[a-z]+", text.lower()))
        assert not words & set(BANNED_WORDS), text
    assert set(LEVEL_DESCRIPTIONS) == {"Claimed", "Used", "Applied", "Demonstrated"}


def test_badge_contains_verified_achievement_only_and_is_unsigned() -> None:
    from app.services.badge import create_badge

    badge = create_badge(fake_passport("passport-1"))
    subject = badge["credentialSubject"]
    assert [(item["name"], item["skillId"]) for item in subject["achievement"]] == [("Python", "python")]
    assert badge["prototypeNote"] == "unsigned prototype"
    assert "proof" not in badge
    assert "credentialStatus" not in badge


def test_jobs_persistence_and_error() -> None:
    from app.services.jobs import complete_job, create_job, fail_job, get_job, update_job

    job_id = "test-persistent-job"
    create_job(job_id)
    queued = get_job(job_id)
    assert queued is not None
    assert queued["status"] == "queued"
    assert queued["step"] == "Queued"

    update_job(job_id, "Reading resume")
    running = get_job(job_id)
    assert running is not None
    assert running["status"] == "running"
    assert running["step"] == "Reading resume"
    assert running["completed_steps"] == ["Reading GitHub"]

    fail_job(job_id, "Temporary error")
    failed = get_job(job_id)
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["error"] == "Temporary error"

    complete_job(job_id, "passport-abc")
    done = get_job(job_id)
    assert done is not None
    assert done["status"] == "done"
    assert done["passport_id"] == "passport-abc"


def test_certificate_upload_count_and_aggregate_size_limits(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "test.sqlite")
    client = TestClient(main_module.app)
    resume = ("resume.pdf", b"%PDF-1.4 " + b"x" * 1024, "application/pdf")

    # 1. More than 5 certificates is rejected
    six_certs = [("cert.pdf", b"%PDF-1.4 cert", "application/pdf")] * 6
    res = client.post(
        "/passport/create",
        data={"github_username": "student-certs", "target_role": "backend-intern"},
        files=[("resume", resume)] + [("certificates", cert) for cert in six_certs],
    )
    assert res.status_code == 422
    assert "At most 5 certificates" in res.json()["detail"]

    # 2. Total upload exceeding 25 MB aggregate limit is rejected
    big_resume = ("resume.pdf", b"%PDF-1.4 " + b"a" * (8 * 1024 * 1024 + 512 * 1024), "application/pdf")
    big_cert1 = ("c1.pdf", b"%PDF-1.4 " + b"b" * (8 * 1024 * 1024 + 512 * 1024), "application/pdf")
    big_cert2 = ("c2.pdf", b"%PDF-1.4 " + b"c" * (8 * 1024 * 1024 + 512 * 1024), "application/pdf")
    res_big = client.post(
        "/passport/create",
        data={"github_username": "student-heavy", "target_role": "backend-intern"},
        files=[("resume", big_resume), ("certificates", big_cert1), ("certificates", big_cert2)],
    )
    assert res_big.status_code == 413
    assert "Total upload size across resume and certificates must be 25 MB or smaller" in res_big.json()["detail"]


def test_debug_endpoints_disabled_or_protected(tmp_path, monkeypatch) -> None:
    from app import config
    client = TestClient(main_module.app)

    # 1. When ALLOW_DEBUG_ENDPOINTS is False, returns 404
    monkeypatch.setattr(config, "ALLOW_DEBUG_ENDPOINTS", False)
    assert client.get("/debug/github/testuser").status_code == 404

    # 2. When REQUIRE_RECRUITER_AUTH is active, debug routes fail closed without recruiter key
    monkeypatch.setattr(config, "ALLOW_DEBUG_ENDPOINTS", True)
    monkeypatch.setattr(config, "REQUIRE_RECRUITER_AUTH", True)
    monkeypatch.setattr(config, "RECRUITER_KEY", "secret-key")
    monkeypatch.setattr(main_module, "RECRUITER_KEY", "secret-key")
    monkeypatch.setattr(main_module, "ALLOW_DEBUG_ENDPOINTS", True)
    if not any(route.path == "/debug/github/{username}" for route in main_module.app.routes):
        main_module.app.include_router(main_module.debug_router)

    res_unauth = client.get("/debug/github/testuser")
    assert res_unauth.status_code == 401

    # 3. Resume debug upload size enforcement
    big_pdf = b"%PDF-1.4 " + b"x" * (11 * 1024 * 1024)
    res_oversized = client.post(
        "/debug/resume",
        files={"resume": ("resume.pdf", big_pdf, "application/pdf")},
        headers={"X-Recruiter-Key": "secret-key"},
    )
    assert res_oversized.status_code == 413


def test_recheck_concurrent_job_and_rate_limit(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    access_token = REVIEW_OWNER_TOKEN
    storage.create_stored_job(
        "p-review",
        target="demo-user",
        access_token_hash=hashlib.sha256(access_token.encode()).hexdigest(),
    )
    storage.complete_stored_job("p-review", "p-review", [])
    # 1. While a job is running for the candidate, recheck returns 409
    storage.create_stored_job("active-recheck-job", target="demo-user")
    storage.update_stored_job("active-recheck-job", "Reading GitHub", [])
    res_conflict = client.post(
        "/passport/p-review/recheck",
        headers={"X-Passport-Token": access_token},
    )
    assert res_conflict.status_code == 409
    assert "already in progress" in res_conflict.json()["detail"]


def test_recheck_requires_owner_token_and_hides_upstream_error(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    access_token = REVIEW_OWNER_TOKEN
    storage.create_stored_job(
        "p-review",
        target="demo-user",
        access_token_hash=hashlib.sha256(access_token.encode()).hexdigest(),
    )
    storage.complete_stored_job("p-review", "p-review", [])
    monkeypatch.setattr(
        main_module,
        "run_recheck_job",
        lambda job_id, _passport: storage.fail_stored_job(job_id, "private upstream response"),
    )
    assert client.post(
        "/passport/p-review/recheck",
        headers={"X-Passport-Token": ""},
    ).status_code == 404

    response = client.post(
        "/passport/p-review/recheck",
        headers={"X-Passport-Token": access_token},
    )
    assert response.status_code == 202
    job = client.get(
        f"/jobs/{response.json()['job_id']}",
        headers={"X-Passport-Token": response.json()["access_token"]},
    )
    assert job.json()["status"] == "failed"
    assert "private upstream response" not in job.text


def test_job_status_requires_token_and_redacts_job_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "test.sqlite")
    access_token = "owner-session-secret"
    token_hash = hashlib.sha256(access_token.encode()).hexdigest()
    storage.create_stored_job("failed-job", access_token_hash=token_hash)
    storage.fail_stored_job("failed-job", "private provider response")
    client = TestClient(main_module.app)

    assert client.get("/jobs/failed-job").status_code == 404
    response = client.get(
        "/jobs/failed-job",
        headers={"X-Passport-Token": access_token},
    )
    assert response.status_code == 200
    assert response.json()["error"] == "Passport generation failed. Please try again."
    assert "private provider response" not in response.text


def test_commit_signature_head_scope_labeling() -> None:
    from app.services.verifier import _commit_signature_signal
    # When scope is head, it explicitly identifies head commit scope
    head_source = {
        "commit_verification": {"verified": True},
        "commit_verification_scope": "head",
    }
    signal = _commit_signature_signal(head_source)
    assert signal["label"] == "Head commit cryptographic signature"
    assert "Individual historical commit for cited lines was not separately verified" in signal["detail"]

    # When scope is cited, it confirms the commit is cryptographically signed
    cited_source = {
        "commit_verification": {"verified": True},
        "commit_verification_scope": "cited",
    }
    signal_cited = _commit_signature_signal(cited_source)
    assert signal_cited["label"] == "Cryptographic commit signature"


def test_debug_routes_absent_when_app_env_production(monkeypatch) -> None:
    import importlib
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ALLOW_DEBUG_ENDPOINTS", raising=False)
    from app import config
    importlib.reload(config)
    from app import main
    importlib.reload(main)
    try:
        debug_paths = [r.path for r in main.app.routes if r.path.startswith("/debug")]
        assert debug_paths == []
        client = TestClient(main.app)
        assert client.get("/debug/github/octocat").status_code == 404
        assert client.post("/debug/resume").status_code == 404
    finally:
        monkeypatch.setenv("APP_ENV", "development")
        importlib.reload(config)
        importlib.reload(main)


def test_debug_routes_disabled_by_default(monkeypatch) -> None:
    import importlib
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("ALLOW_DEBUG_ENDPOINTS", raising=False)
    from app import config
    importlib.reload(config)
    from app import main
    importlib.reload(main)
    try:
        assert config.ALLOW_DEBUG_ENDPOINTS is False
        assert not any(r.path.startswith("/debug") for r in main.app.routes)
    finally:
        monkeypatch.setenv("APP_ENV", "development")
        importlib.reload(config)
        importlib.reload(main)


def test_app_environment_defaults_to_production(monkeypatch) -> None:
    import importlib
    from app import config

    monkeypatch.delenv("APP_ENV", raising=False)
    importlib.reload(config)
    try:
        assert config.APP_ENV == "production"
        assert config.REQUIRE_RECRUITER_AUTH is True
    finally:
        monkeypatch.setenv("APP_ENV", "development")
        importlib.reload(config)


def test_passport_create_atomic_reservation_duplicate_and_capacity(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "test_reserve.db"
    monkeypatch.setattr(storage, "DATABASE_PATH", db_file)
    storage.initialize_storage()

    client = TestClient(main_module.app)
    resume_pdf = ("resume.pdf", b"%PDF-1.4 valid", "application/pdf")
    monkeypatch.setattr(main_module, "run_passport_job", lambda *args, **kwargs: None)

    # 1. First reservation succeeds
    res1 = client.post(
        "/passport/create",
        data={"github_username": "CandidateAlpha", "target_role": "backend-intern"},
        files={"resume": resume_pdf},
    )
    assert res1.status_code == 202
    job1_id = res1.json()["job_id"]
    assert job1_id

    # 2. Duplicate reservation for normalized candidate key (case-insensitive, trimmed) returns 409
    res_dup = client.post(
        "/passport/create",
        data={"github_username": " candidatealpha ", "target_role": "backend-intern"},
        files={"resume": resume_pdf},
    )
    assert res_dup.status_code == 409
    assert "already in progress" in res_dup.json()["detail"]

    # 3. Add 4 more active jobs to hit the max limit of 5
    for i in range(2, 6):
        res = client.post(
            "/passport/create",
            data={"github_username": f"Candidate_{i}", "target_role": "backend-intern"},
            files={"resume": resume_pdf},
        )
        assert res.status_code == 202

    # 4. 6th simultaneous reservation exceeds max_active=5 capacity and returns 429
    res_cap = client.post(
        "/passport/create",
        data={"github_username": "Candidate_Overflow", "target_role": "backend-intern"},
        files={"resume": resume_pdf},
    )
    assert res_cap.status_code == 429
    assert "maximum concurrent verification jobs" in res_cap.json()["detail"]


def test_persistent_shared_storage_rate_limit(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "test_rate.db"
    monkeypatch.setattr(storage, "DATABASE_PATH", db_file)
    storage.initialize_storage()

    key = "127.0.0.1:create_passport"
    # Allow 3 requests per 10 seconds
    assert storage.check_storage_rate_limit(key, max_requests=3, window_seconds=10.0) is True
    assert storage.check_storage_rate_limit(key, max_requests=3, window_seconds=10.0) is True
    assert storage.check_storage_rate_limit(key, max_requests=3, window_seconds=10.0) is True
    # 4th request exceeds rate limit
    assert storage.check_storage_rate_limit(key, max_requests=3, window_seconds=10.0) is False

    # Simulate another worker or process restart reading the same database
    assert storage.check_storage_rate_limit(key, max_requests=3, window_seconds=10.0) is False

    # Different IP/key is unaffected
    assert storage.check_storage_rate_limit("192.168.1.1:create_passport", max_requests=3, window_seconds=10.0) is True


def test_rate_limiter_storage_failure_fails_closed_with_503(monkeypatch) -> None:
    def broken_limiter(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(main_module, "check_storage_rate_limit", broken_limiter)
    client = TestClient(main_module.app)
    res = client.post(
        "/passport/create",
        data={"github_username": "testuser", "target_role": "backend-intern"},
        files={"resume": ("resume.pdf", b"%PDF-1.4", "application/pdf")},
    )
    # Fail closed with 503 Service Unavailable, not silent downgrade
    assert res.status_code == 503
    assert "Rate limiting service temporarily unavailable" in res.json()["detail"]


def test_rate_limit_uses_forwarded_ip_only_from_configured_proxies(monkeypatch) -> None:
    from starlette.requests import Request

    monkeypatch.setattr(main_module, "TRUSTED_PROXY_IPS", frozenset({"10.0.0.1", "10.0.0.2"}))

    def make_request(peer: str, forwarded: str) -> Request:
        return Request({
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", forwarded.encode())],
            "client": (peer, 1234),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        })

    trusted = make_request("10.0.0.1", "203.0.113.10, 10.0.0.2")
    untrusted = make_request("198.51.100.7", "203.0.113.99")
    spoofed_chain = make_request("10.0.0.1", "203.0.113.99, 198.51.100.8")
    assert main_module._rate_limit_client_ip(trusted) == "203.0.113.10"
    assert main_module._rate_limit_client_ip(untrusted) == "198.51.100.7"
    assert main_module._rate_limit_client_ip(spoofed_chain) == "198.51.100.8"

    monkeypatch.setattr(main_module, "TRUSTED_PROXY_IPS", frozenset({"2001:db8::1"}))
    equivalent_ipv6 = make_request("2001:0db8:0:0:0:0:0:1", "203.0.113.10")
    assert main_module._rate_limit_client_ip(equivalent_ipv6) == "203.0.113.10"


def test_multi_worker_startup_reconciliation_preserves_active_jobs(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "test_reconcile.db"
    monkeypatch.setattr(storage, "DATABASE_PATH", db_file)
    storage.initialize_storage()

    # Worker A creates an active job just now
    storage.create_stored_job("active-worker-job", target="candidate-worker")
    storage.update_stored_job("active-worker-job", "Reading GitHub", [])

    # A dead job created 2 hours ago from a dead worker
    with storage._connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, status, step, completed_steps, error, passport_id, target, created_at, updated_at)
            VALUES ('dead-job', 'running', 'Reading resume', '[]', NULL, NULL, 'dead-candidate',
                    datetime('now', '-7200 seconds'), datetime('now', '-7200 seconds'))
            """
        )

    # Worker B starts up and runs reconciliation with 10-minute timeout
    reconciled_count = storage.reconcile_interrupted_jobs(stale_seconds=600)
    assert reconciled_count == 1

    # Active job remains running; dead job was marked failed
    active_job = storage.get_stored_job("active-worker-job")
    assert active_job["status"] == "running"

    dead_job = storage.get_stored_job("dead-job")
    assert dead_job["status"] == "failed"
    assert "interrupted or timed out" in dead_job["error"]


def test_upload_validation_failure_cleans_up_reserved_job(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "test_cleanup.db"
    monkeypatch.setattr(storage, "DATABASE_PATH", db_file)
    storage.initialize_storage()

    client = TestClient(main_module.app)
    # Upload with non-PDF certificate triggers 415 during file processing
    res = client.post(
        "/passport/create",
        data={"github_username": "failing-candidate", "target_role": "backend-intern"},
        files=[
            ("resume", ("resume.pdf", b"%PDF-1.4 valid", "application/pdf")),
            ("certificates", ("cert.txt", b"not a pdf", "text/plain")),
        ],
    )
    assert res.status_code == 415

    # Verify the job is marked failed, so the candidate is NOT locked out by 409
    active = storage.has_active_job_for_target("failing-candidate")
    assert active is False


def test_request_body_limit_rejects_before_multipart_parsing() -> None:
    client = TestClient(main_module.app)
    response = client.post(
        "/passport/create",
        content=b"x" * (main_module.MAX_REQUEST_BODY_BYTES + 1),
        headers={"Content-Type": "multipart/form-data; boundary=not-parsed"},
    )
    assert response.status_code == 413


def test_request_body_limit_rejects_streamed_multipart_without_content_length() -> None:
    import asyncio

    import httpx

    async def body():
        prefix = (
            b'--test-boundary\r\nContent-Disposition: form-data; name="github_username"\r\n\r\nstudent\r\n'
            b'--test-boundary\r\nContent-Disposition: form-data; name="target_role"\r\n\r\nbackend-intern\r\n'
            b'--test-boundary\r\nContent-Disposition: form-data; name="resume"; filename="resume.pdf"\r\n'
            b'Content-Type: application/pdf\r\n\r\n%PDF-1.4 '
        )
        yield prefix
        remaining = main_module.MAX_REQUEST_BODY_BYTES + 1 - len(prefix)
        chunk = b"x" * (64 * 1024)
        while remaining:
            part = chunk[:remaining]
            yield part
            remaining -= len(part)

    async def request():
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/passport/create",
                content=body(),
                headers={"Content-Type": "multipart/form-data; boundary=test-boundary"},
            )

    response = asyncio.run(request())
    assert response.status_code == 413
