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


def _review_client(tmp_path, monkeypatch, recruiter_key: str = "") -> TestClient:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(main_module, "RECRUITER_KEY", recruiter_key)
    storage.save_passport(passport_with_fraud_signals("p-review"))
    return TestClient(main_module.app)


STUDENT_WORDS_NOT_ALLOWED = ("fraud", "fake", "copied", "cheat")


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


def test_new_and_legacy_earlier_source_kinds_are_both_handled(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    review = client.get("/passport/p-review/review").json()["rejected_claims"]
    assert {item["kind"] for item in review} == {"unsupported", "copied", "earlier_source"}
    # Both kinds can be the subject of a review request.
    for key in ("python:demo:legacy.py:1-9", EXCLUDED_KEY):
        assert client.post("/passport/p-review/review-requests", json={"citation_key": key}).status_code == 201


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
    codes = [
        client.post("/passport/p-many/review-requests",
                    json={"citation_key": f"python:demo:helpers.py:{index}-{index + 1}"}).status_code
        for index in range(1, 12)
    ]
    assert codes == [201] * 10 + [429]


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

    monkeypatch.setattr(main_module, "recheck_passport", rebuild)
    student = client.post("/passport/p-review/recheck").json()["passport"]
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


def test_recheck_returns_student_view(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main_module, "recheck_passport",
        lambda existing: {**passport_with_fraud_signals(existing["id"]), "level_changes": []},
    )
    keys, values = _keys_and_values(client.post("/passport/p-review/recheck").json())
    assert not {"integrity", "good_answer_mentions"} & keys
    assert "copied" not in values


def test_recheck_endpoint_calls_the_pipeline_not_itself(tmp_path, monkeypatch) -> None:
    from app.services import pipeline

    # The endpoint used to share the pipeline function's name and called itself (502 on every Re-check).
    assert main_module.recheck_passport is pipeline.recheck_passport
    client = _review_client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(pipeline, "build_passport", lambda **kwargs: calls.append(kwargs) or {
        **passport_with_fraud_signals("p-review"), "level_changes": []})
    response = client.post("/passport/p-review/recheck")
    assert response.status_code == 200
    assert calls[0]["passport_id"] == "p-review" and calls[0]["force_refresh"] is True


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


def test_coverage_is_in_student_and_review_responses(tmp_path, monkeypatch) -> None:
    client = _review_client(tmp_path, monkeypatch)
    passport = passport_with_fraud_signals("p-coverage")
    passport["coverage"] = {"repos_analysed": 2, "source_files_read": 9, "source_files_total": 40,
                            "lines_read": 1200, "repos_skipped": [{"name": "old", "reason": "fork"}]}
    storage.save_passport(passport)
    for path in ("/passport/p-coverage", "/passport/p-coverage/review"):
        assert client.get(path).json()["coverage"] == passport["coverage"]


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
    assert [item["name"] for item in subject["achievement"]] == ["python"]
    assert badge["prototypeNote"] == "unsigned prototype"
    assert "proof" not in badge
    assert "credentialStatus" not in badge
