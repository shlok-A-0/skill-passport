import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config
from app.services import identity, storage
from app.services.identity import (
    OwnershipError,
    create_challenge,
    identity_signals,
    latest_ownership,
    ownership_signal,
    resume_github_logins,
    resume_link_signal,
    verify_challenge,
)
from app.services.verifier import _backdated_commit_signal, integrity_signals


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    # Rate limits live in the same SQLite file, so a fresh database also resets them.
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "identity.sqlite")

    def no_network(username):
        raise AssertionError("tests must not call GitHub")

    monkeypatch.setattr(identity, "_fetch_recent_gists", no_network)


def gist(description: str, login: str, public: bool = True) -> dict:
    return {
        "description": description,
        "owner": {"login": login},
        "public": public,
        "html_url": f"https://gist.github.com/{login}/0123abcd",
    }


def serve_gists(monkeypatch, gists: list[dict]) -> None:
    monkeypatch.setattr(identity, "_fetch_recent_gists", lambda username: gists)


def test_gist_with_the_code_proves_ownership_once(monkeypatch) -> None:
    challenge = create_challenge(" JaneDoe ")
    assert challenge["github_username"] == "JaneDoe"
    assert challenge["code"].startswith("skill-passport-verify-")
    serve_gists(monkeypatch, [gist("dotfiles", "janedoe"), gist(f"Verifying {challenge['code']}", "janedoe")])

    result = verify_challenge(challenge["challenge_id"], "janedoe")
    assert result["verified"] is True and result["method"] == "gist"
    assert result["gist_url"] == "https://gist.github.com/janedoe/0123abcd" and result["verified_at"]
    assert latest_ownership("JANEDOE")["gist_url"] == result["gist_url"]
    signal = ownership_signal("JaneDoe", is_demo=False)
    assert signal["status"] == "ok" and "public gist" in signal["detail"]
    assert result["verified_at"][:10] in signal["detail"]

    with pytest.raises(OwnershipError, match="already used"):
        verify_challenge(challenge["challenge_id"], "JaneDoe")


def test_someone_elses_username_cannot_be_proven(monkeypatch) -> None:
    challenge = create_challenge("torvalds")
    # The real account owner never published this code.
    serve_gists(monkeypatch, [gist("kernel notes", "torvalds")])
    with pytest.raises(OwnershipError, match=challenge["code"]):
        verify_challenge(challenge["challenge_id"], "torvalds")
    assert latest_ownership("torvalds") is None
    assert ownership_signal("torvalds", is_demo=False)["status"] == "warn"


def test_code_is_bound_to_its_username_and_expires(monkeypatch) -> None:
    challenge = create_challenge("janedoe")
    serve_gists(monkeypatch, [gist(challenge["code"], "janedoe")])
    with pytest.raises(OwnershipError, match="different GitHub username"):
        verify_challenge(challenge["challenge_id"], "someone-else")
    with pytest.raises(OwnershipError, match="not found"):
        verify_challenge("made-up-id", "janedoe")
    with pytest.raises(OwnershipError):
        verify_challenge(None, "janedoe")

    later = identity._now() + timedelta(hours=2)
    monkeypatch.setattr(identity, "_now", lambda: later)
    with pytest.raises(OwnershipError, match="expired"):
        verify_challenge(challenge["challenge_id"], "janedoe")
    assert latest_ownership("janedoe") is None


def test_gist_owned_by_another_account_or_secret_does_not_count(monkeypatch) -> None:
    challenge = create_challenge("janedoe")
    serve_gists(monkeypatch, [
        gist(challenge["code"], "impostor"),
        gist(challenge["code"], "janedoe", public=False),
    ])
    with pytest.raises(OwnershipError, match="No public gist"):
        verify_challenge(challenge["challenge_id"], "janedoe")
    assert latest_ownership("janedoe") is None


def _create(client: TestClient, **data: str):
    return client.post(
        "/passport/create",
        data={"github_username": "janedoe", "target_role": "backend-intern", **data},
        files={"resume": ("resume.pdf", b"%PDF-1.4 test", "application/pdf")},
    )


def test_create_needs_an_ownership_proof_for_real_passports(monkeypatch) -> None:
    monkeypatch.setattr(config, "REQUIRE_OWNERSHIP_PROOF", True)
    started = []
    monkeypatch.setattr(main_module, "run_passport_job", lambda *args: started.append(args))
    client = TestClient(main_module.app)

    denied = _create(client)
    assert denied.status_code == 403
    assert "Prove this GitHub account is yours" in denied.json()["detail"]
    assert started == []
    # The reserved job is released, so the candidate isn't locked out with a 409.
    assert not storage.has_active_job_for_target("janedoe")

    assert client.post("/ownership/challenge", json={"github_username": ""}).status_code == 422
    assert client.post("/ownership/challenge", json={"github_username": "x" * 40}).status_code == 422
    response = client.post("/ownership/challenge", json={"github_username": "janedoe"})
    assert response.status_code == 200
    challenge = response.json()
    assert set(challenge) == {"challenge_id", "github_username", "code", "expires_at", "instructions"}
    assert challenge["code"] in challenge["instructions"] and "https://gist.github.com" in challenge["instructions"]

    serve_gists(monkeypatch, [gist(challenge["code"], "janedoe")])
    accepted = _create(client, ownership_challenge_id=challenge["challenge_id"])
    assert accepted.status_code == 202
    assert len(started) == 1 and started[0][1] == "janedoe"


def test_demo_passports_skip_the_ownership_proof(monkeypatch) -> None:
    monkeypatch.setattr(config, "REQUIRE_OWNERSHIP_PROOF", True)
    monkeypatch.setattr(main_module, "run_passport_job", lambda *args: None)
    assert _create(TestClient(main_module.app), is_demo="true").status_code == 202
    assert ownership_signal("janedoe", is_demo=True)["status"] == "unknown"


def test_resume_github_links_are_extracted_and_compared() -> None:
    text = "Portfolio: github.com/JaneDoe\nProject: github.com/janedoe/repo\nWork: github.com/orgs/acme"
    assert resume_github_logins(text) == ["janedoe"]
    assert resume_github_logins("https://www.github.com/acme-labs/tool and gist.github.com/other") == ["acme-labs"]

    assert resume_link_signal("JaneDoe", ["janedoe"])["status"] == "ok"
    other = resume_link_signal("janedoe", ["acme-labs"])
    assert other["status"] == "warn"
    assert "github.com/acme-labs" in other["detail"] and "ask the candidate" in other["detail"]
    assert resume_link_signal("janedoe", [])["status"] == "unknown"


def test_identity_signals_keep_the_resume_signal_on_recheck_and_are_recruiter_only() -> None:
    first = identity_signals("janedoe", False, ["janedoe"])
    assert [signal["id"] for signal in first] == ["account_ownership", "resume_github_link"]
    assert [signal["status"] for signal in first] == ["warn", "ok"]
    # Re-check: the resume isn't stored, so the earlier resume signal is kept.
    assert identity_signals("janedoe", False, None, first)[1] == first[1]
    assert identity_signals("janedoe", False, None)[1]["status"] == "unknown"

    passport = {"id": "p-identity", "identity": first, "skills": [], "rejected_claims": [], "interview_kit": []}
    assert "identity" not in main_module.student_view(passport)
    assert passport["identity"] == first


def _commits(*recorded_after_days: int) -> list[dict]:
    authored = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {"sha": str(index), "author_date": authored.isoformat(),
         "committer_date": (authored + timedelta(days=days)).isoformat()}
        for index, days in enumerate(recorded_after_days)
    ]


def test_backdated_commit_signal() -> None:
    warn = _backdated_commit_signal({"user_commits": _commits(30, 30, 0)})
    assert warn["id"] == "backdated_commits" and warn["status"] == "warn"
    assert "rebases" in warn["detail"]
    assert _backdated_commit_signal({"user_commits": _commits(30, 0, 0)})["status"] == "ok"
    # Too few commits to judge.
    assert _backdated_commit_signal({"user_commits": _commits(30, 30)})["status"] == "ok"
    assert _backdated_commit_signal({"user_commits": [{"sha": "a", "author_date": "2026-01-01T00:00:00Z"}]})["status"] == "unknown"
    assert _backdated_commit_signal({})["status"] == "unknown"

    ids = [signal["id"] for signal in integrity_signals({}, {}, 1, 1, "janedoe")]
    assert ids.index("backdated_commits") == ids.index("history_import") + 1
