import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config
from app.services import identity, storage
from app.services.identity import (
    OwnershipError,
    create_challenge,
    identity_signals,
    ownership_for_passport,
    ownership_signal,
    resume_github_logins,
    resume_link_signal,
    verify_challenge,
)
from app.services.verifier import _backdated_commit_signal, integrity_signals

fetch_recent_gists = identity._fetch_recent_gists


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


def prove(monkeypatch, username: str, passport_id: str) -> dict:
    """A successful proof for `username`, recorded against `passport_id`."""
    challenge = create_challenge(username)
    serve_gists(monkeypatch, [gist(challenge["code"], username)])
    return verify_challenge(challenge["challenge_id"], username, passport_id)


def test_gist_with_the_code_proves_ownership_once(monkeypatch) -> None:
    challenge = create_challenge(" JaneDoe ")
    assert challenge["github_username"] == "JaneDoe"
    assert challenge["code"].startswith("skill-passport-verify-")
    serve_gists(monkeypatch, [gist("dotfiles", "janedoe"), gist(f"Verifying {challenge['code']}", "janedoe")])

    result = verify_challenge(challenge["challenge_id"], "janedoe", "p-1")
    assert result["verified"] is True and result["method"] == "gist"
    assert result["gist_url"] == "https://gist.github.com/janedoe/0123abcd" and result["verified_at"]
    assert ownership_for_passport("p-1")["gist_url"] == result["gist_url"]

    with pytest.raises(OwnershipError, match="already used"):
        verify_challenge(challenge["challenge_id"], "JaneDoe", "p-2")
    assert ownership_for_passport("p-2") is None


def test_someone_elses_username_cannot_be_proven(monkeypatch) -> None:
    challenge = create_challenge("torvalds")
    # The real account owner never published this code.
    serve_gists(monkeypatch, [gist("kernel notes", "torvalds")])
    with pytest.raises(OwnershipError, match=challenge["code"]):
        verify_challenge(challenge["challenge_id"], "torvalds", "p-1")
    assert ownership_for_passport("p-1") is None
    assert ownership_signal("p-1", "torvalds", is_demo=False)["status"] == "warn"


def test_code_is_bound_to_its_username_and_expires(monkeypatch) -> None:
    challenge = create_challenge("janedoe")
    serve_gists(monkeypatch, [gist(challenge["code"], "janedoe")])
    with pytest.raises(OwnershipError, match="different GitHub username"):
        verify_challenge(challenge["challenge_id"], "someone-else", "p-1")
    with pytest.raises(OwnershipError, match="not found"):
        verify_challenge("made-up-id", "janedoe", "p-1")
    with pytest.raises(OwnershipError):
        verify_challenge(None, "janedoe", "p-1")

    later = identity._now() + timedelta(hours=2)
    monkeypatch.setattr(identity, "_now", lambda: later)
    with pytest.raises(OwnershipError, match="expired"):
        verify_challenge(challenge["challenge_id"], "janedoe", "p-1")
    assert ownership_for_passport("p-1") is None


@pytest.mark.parametrize(
    ("status", "headers", "body", "expected"),
    [
        (403, {"X-RateLimit-Remaining": "0"}, {"message": "API rate limit exceeded"}, "rate-limited"),
        (403, {"X-RateLimit-Remaining": "9"}, {"message": "API rate limit exceeded"}, "rate-limited"),
        (429, {"X-RateLimit-Remaining": "9"}, {"message": "Too many requests"}, "rate-limited"),
        (403, {"X-RateLimit-Remaining": "9"}, {"message": "Resource not accessible by integration"}, "denied access"),
    ],
)
def test_github_gist_forbidden_errors_are_distinguished(monkeypatch, status, headers, body, expected) -> None:
    response = httpx.Response(status, headers=headers, json=body)
    monkeypatch.setattr(identity, "_fetch_recent_gists", fetch_recent_gists)
    monkeypatch.setattr(identity.httpx, "get", lambda *args, **kwargs: response)

    with pytest.raises(OwnershipError, match=expected):
        identity._fetch_recent_gists("janedoe")


def test_new_challenge_prunes_expired_unused_but_keeps_active_and_verified_rows(monkeypatch) -> None:
    expired = create_challenge("expired-user")
    active = create_challenge("active-user")
    verified = create_challenge("verified-user")
    serve_gists(monkeypatch, [gist(verified["code"], "verified-user")])
    verify_challenge(verified["challenge_id"], "verified-user", "p-verified")

    past = (identity._now() - timedelta(hours=1)).isoformat()
    with identity._connect() as connection:
        connection.execute(
            "UPDATE ownership_challenges SET expires_at = ? WHERE id = ?",
            (past, expired["challenge_id"]),
        )
        connection.execute(
            "UPDATE ownership_challenges SET expires_at = ? WHERE id = ?",
            (past, verified["challenge_id"]),
        )

    create_challenge("next-user")

    with identity._connect() as connection:
        remaining = {
            row["id"]
            for row in connection.execute("SELECT id FROM ownership_challenges")
        }
    assert expired["challenge_id"] not in remaining
    assert active["challenge_id"] in remaining
    assert verified["challenge_id"] in remaining
    assert ownership_for_passport("p-verified") is not None


def test_gist_owned_by_another_account_or_secret_does_not_count(monkeypatch) -> None:
    challenge = create_challenge("janedoe")
    serve_gists(monkeypatch, [
        gist(challenge["code"], "impostor"),
        gist(challenge["code"], "janedoe", public=False),
    ])
    with pytest.raises(OwnershipError, match="No public gist"):
        verify_challenge(challenge["challenge_id"], "janedoe", "p-1")
    assert ownership_for_passport("p-1") is None


def test_a_proof_must_name_the_passport_it_authorises(monkeypatch) -> None:
    challenge = create_challenge("janedoe")
    serve_gists(monkeypatch, [gist(challenge["code"], "janedoe")])
    with pytest.raises(ValueError):
        verify_challenge(challenge["challenge_id"], "janedoe", "")
    # The code was not consumed by the refused call.
    assert verify_challenge(challenge["challenge_id"], "janedoe", "p-1")["verified"] is True


# --- Ownership is bound to the passport whose creation the proof authorised ---


def test_verified_passport_shows_ownership_with_its_own_proof(monkeypatch) -> None:
    result = prove(monkeypatch, "janedoe", "p-1")
    signal = identity_signals("p-1", "JaneDoe", False, ["janedoe"])[0]
    assert signal["status"] == "ok"
    assert "public gist" in signal["detail"] and result["verified_at"][:10] in signal["detail"]
    assert signal["gist_url"] == result["gist_url"] and signal["verified_at"] == result["verified_at"]


def test_second_passport_for_the_same_username_does_not_inherit_the_proof(monkeypatch) -> None:
    prove(monkeypatch, "janedoe", "p-1")
    assert ownership_signal("p-1", "janedoe", is_demo=False)["status"] == "ok"
    other = ownership_signal("p-2", "janedoe", is_demo=False)
    assert other["status"] == "warn" and "gist_url" not in other


def test_proof_for_one_account_does_not_verify_a_passport_for_another(monkeypatch) -> None:
    prove(monkeypatch, "janedoe", "p-1")
    assert ownership_signal("p-1", "someone-else", is_demo=False)["status"] == "warn"


def test_demo_passports_never_show_ownership_verified(monkeypatch) -> None:
    # Even a proof recorded against the same id doesn't make a demo passport "ok".
    prove(monkeypatch, "janedoe", "p-demo")
    assert ownership_signal("p-demo", "janedoe", is_demo=True)["status"] == "unknown"
    prove(monkeypatch, "janedoe", "p-real")
    assert ownership_signal("p-other-demo", "janedoe", is_demo=True)["status"] == "unknown"


def test_failed_expired_and_reused_challenges_establish_nothing(monkeypatch) -> None:
    failed = create_challenge("janedoe")
    serve_gists(monkeypatch, [gist("unrelated", "janedoe")])
    with pytest.raises(OwnershipError):
        verify_challenge(failed["challenge_id"], "janedoe", "p-failed")

    expired = create_challenge("janedoe")
    serve_gists(monkeypatch, [gist(expired["code"], "janedoe")])
    later = identity._now() + timedelta(hours=2)
    monkeypatch.setattr(identity, "_now", lambda: later)
    with pytest.raises(OwnershipError, match="expired"):
        verify_challenge(expired["challenge_id"], "janedoe", "p-expired")
    monkeypatch.setattr(identity, "_now", lambda: datetime.now(timezone.utc))

    reused = create_challenge("janedoe")
    serve_gists(monkeypatch, [gist(reused["code"], "janedoe")])
    verify_challenge(reused["challenge_id"], "janedoe", "p-first")
    with pytest.raises(OwnershipError, match="already used"):
        verify_challenge(reused["challenge_id"], "janedoe", "p-reused")

    for passport_id in ("p-failed", "p-expired", "p-reused"):
        assert ownership_for_passport(passport_id) is None
        assert ownership_signal(passport_id, "janedoe", is_demo=False)["status"] == "warn"
    assert ownership_signal("p-first", "janedoe", is_demo=False)["status"] == "ok"


def test_concurrent_use_of_one_code_verifies_only_one_passport(monkeypatch) -> None:
    challenge = create_challenge("janedoe")
    published = [gist(challenge["code"], "janedoe")]
    calls = []

    def fetch(username):
        calls.append(username)
        if len(calls) == 1:
            # A second request uses the same code while this one is waiting on GitHub.
            verify_challenge(challenge["challenge_id"], "janedoe", "p-a")
        return published

    monkeypatch.setattr(identity, "_fetch_recent_gists", fetch)
    with pytest.raises(OwnershipError, match="already used"):
        verify_challenge(challenge["challenge_id"], "janedoe", "p-b")
    assert ownership_for_passport("p-a") is not None
    assert ownership_for_passport("p-b") is None


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
    # The proof is recorded against the passport this job creates (passport id = job id).
    job_id = accepted.json()["job_id"]
    assert started[0][0] == job_id
    assert ownership_for_passport(job_id)["gist_url"] == "https://gist.github.com/janedoe/0123abcd"
    assert ownership_signal(job_id, "janedoe", is_demo=False)["status"] == "ok"


def test_demo_passports_skip_the_ownership_proof(monkeypatch) -> None:
    monkeypatch.setattr(config, "REQUIRE_OWNERSHIP_PROOF", True)
    monkeypatch.setattr(main_module, "run_passport_job", lambda *args: None)
    challenge = create_challenge("janedoe")
    response = _create(TestClient(main_module.app), is_demo="true", ownership_challenge_id=challenge["challenge_id"])
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    # A demo passport neither consumes the code nor shows ownership as verified.
    assert ownership_for_passport(job_id) is None
    assert ownership_signal(job_id, "janedoe", is_demo=True)["status"] == "unknown"


def test_passport_created_with_proof_disabled_does_not_show_ownership_verified(monkeypatch) -> None:
    prove(monkeypatch, "janedoe", "p-earlier")  # the real owner proved it once, for another passport
    monkeypatch.setattr(config, "REQUIRE_OWNERSHIP_PROOF", False)
    started = []
    monkeypatch.setattr(main_module, "run_passport_job", lambda *args: started.append(args))
    response = _create(TestClient(main_module.app))
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert ownership_for_passport(job_id) is None
    assert ownership_signal(job_id, "janedoe", is_demo=False)["status"] == "warn"


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


def test_student_view_exposes_no_identity_verification_details(monkeypatch) -> None:
    result = prove(monkeypatch, "janedoe", "p-identity")
    signals = identity_signals("p-identity", "janedoe", False, ["janedoe"])
    passport = {"id": "p-identity", "identity": signals, "skills": [], "rejected_claims": [], "interview_kit": []}
    keys, values = _keys_and_values(main_module.student_view(passport))
    assert not {"identity", "gist_url", "verified_at"} & keys
    assert result["gist_url"] not in values
    assert passport["identity"] == signals  # the stored passport keeps it for recruiters


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
    first = identity_signals("p-1", "janedoe", False, ["janedoe"])
    assert [signal["id"] for signal in first] == ["account_ownership", "resume_github_link"]
    assert [signal["status"] for signal in first] == ["warn", "ok"]
    # Re-check: the resume isn't stored, so the earlier resume signal is kept.
    assert identity_signals("p-1", "janedoe", False, None, first)[1] == first[1]
    assert identity_signals("p-1", "janedoe", False, None)[1]["status"] == "unknown"

    passport = {"id": "p-identity", "identity": first, "skills": [], "rejected_claims": [], "interview_kit": []}
    assert "identity" not in main_module.student_view(passport)
    assert passport["identity"] == first


# --- Startup refuses a production configuration without the ownership proof ---


@pytest.fixture
def startup_settings(monkeypatch):
    """Set the ownership/environment settings startup() checks; recruiter auth is satisfied."""
    monkeypatch.setattr(main_module, "REQUIRE_RECRUITER_AUTH", True)
    monkeypatch.setattr(main_module, "RECRUITER_ACCOUNTS", {"test-recruiter": "r" * 40})
    monkeypatch.setattr(config, "RECRUITER_ACCOUNTS_SETTING_VALID", True)

    def apply(app_env: str, require_proof: bool, valid: bool = True) -> None:
        monkeypatch.setattr(config, "APP_ENV", app_env)
        monkeypatch.setattr(config, "REQUIRE_OWNERSHIP_PROOF", require_proof)
        monkeypatch.setattr(config, "OWNERSHIP_PROOF_SETTING_VALID", valid)
        monkeypatch.setattr(config, "GITHUB_TOKEN", "test-github-token")
        monkeypatch.setattr(config, "GEMINI_API_KEY", "test-gemini-key")

    return apply


def test_development_may_disable_the_ownership_proof(startup_settings) -> None:
    startup_settings("development", require_proof=False)
    with TestClient(main_module.app) as client:
        assert client.get("/health").status_code == 200


def test_production_starts_with_the_ownership_proof_on(startup_settings) -> None:
    startup_settings("production", require_proof=True)
    with TestClient(main_module.app) as client:
        assert client.get("/health").status_code == 200


def test_production_startup_requires_external_api_credentials(startup_settings, monkeypatch) -> None:
    startup_settings("production", require_proof=True)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError, match="Missing required production setting.*GEMINI_API_KEY"):
        with TestClient(main_module.app):
            pass


@pytest.mark.parametrize("app_env", ["production", "staging", "dev", ""])
def test_startup_refuses_proof_disabled_outside_development(startup_settings, app_env: str) -> None:
    startup_settings(app_env, require_proof=False)
    with pytest.raises(RuntimeError, match="REQUIRE_OWNERSHIP_PROOF=false is only allowed with APP_ENV=development"):
        with TestClient(main_module.app):
            pass


def test_startup_refuses_an_unrecognised_ownership_proof_setting(startup_settings) -> None:
    startup_settings("development", require_proof=True, valid=False)
    with pytest.raises(RuntimeError, match="REQUIRE_OWNERSHIP_PROOF must be true or false"):
        with TestClient(main_module.app):
            pass


@pytest.mark.parametrize(
    ("raw", "required", "valid"),
    [
        (None, True, True),  # missing: on
        ("", True, True),  # empty: on
        ("true", True, True),
        (" YES ", True, True),
        ("1", True, True),
        ("false", False, True),
        ("No", False, True),
        ("0", False, True),
        ("ture", True, False),  # typo: stays on, startup refuses
        ("off", True, False),
    ],
)
def test_ownership_proof_setting_is_parsed_fail_closed(monkeypatch, raw, required, valid) -> None:
    if raw is None:
        monkeypatch.delenv("REQUIRE_OWNERSHIP_PROOF", raising=False)
    else:
        monkeypatch.setenv("REQUIRE_OWNERSHIP_PROOF", raw)
    importlib.reload(config)
    try:
        assert config.REQUIRE_OWNERSHIP_PROOF is required
        assert config.OWNERSHIP_PROOF_SETTING_VALID is valid
    finally:
        monkeypatch.delenv("REQUIRE_OWNERSHIP_PROOF", raising=False)
        importlib.reload(config)


def test_missing_app_env_counts_as_production_for_the_proof_check(monkeypatch, startup_settings) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    importlib.reload(config)
    try:
        assert config.APP_ENV == "production"
        startup_settings(config.APP_ENV, require_proof=False)
        with pytest.raises(RuntimeError, match="only allowed with APP_ENV=development"):
            main_module.startup()
    finally:
        monkeypatch.setenv("APP_ENV", "development")
        importlib.reload(config)


# --- Advisory repo signal ---


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
