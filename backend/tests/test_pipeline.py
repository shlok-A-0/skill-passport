import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import pipeline


def test_recheck_refreshes_ingestion_without_treating_passport_as_job(monkeypatch) -> None:
    existing = {
        "id": "saved-passport",
        "github_username": "student",
        "skills": [{"skill_id": "python", "level": "Applied"}],
        "sources": {"resume": True, "certificates": 0},
        "role_match": {"role_id": "backend-intern"},
        "is_demo": True,
    }
    saved: list[dict] = []
    calls: dict[str, object] = {}
    monkeypatch.setattr(pipeline, "get_passport", lambda _: existing)
    monkeypatch.setattr(
        pipeline,
        "ingest_github",
        lambda username, force_refresh=False: calls.update(
            username=username, force_refresh=force_refresh
        ) or {"username": "MiXeDCase", "repo_count": 1, "repos": []},
    )
    monkeypatch.setattr(pipeline, "propose_claims", lambda *_: [])
    monkeypatch.setattr(
        pipeline,
        "verify_claims",
        lambda *_args, **_kwargs: {
            "skills": [{"skill_id": "python", "level": "Demonstrated", "evidence": []}],
            "rejected_claims": [],
        },
    )
    monkeypatch.setattr(
        pipeline,
        "match_role",
        lambda *_: {"role_id": "backend-intern", "role_name": "Backend Intern"},
    )
    monkeypatch.setattr(
        pipeline,
        "generate_growth_and_kit",
        lambda *_: {"growth_tasks": [], "interview_kit": []},
    )
    monkeypatch.setattr(pipeline, "save_passport", saved.append)
    monkeypatch.setattr(
        pipeline,
        "update_job",
        lambda *_: (_ for _ in ()).throw(AssertionError("re-check is not a job")),
    )
    monkeypatch.setattr(
        pipeline,
        "complete_job",
        lambda *_: (_ for _ in ()).throw(AssertionError("re-check is not a job")),
    )

    result = pipeline.build_passport(
        github_username="mixedcase",
        target_role="backend-intern",
        resume_bytes=b"",
        passport_id="saved-passport",
        force_refresh=True,
        resume_skill_mentions=[],
    )

    assert calls == {"username": "mixedcase", "force_refresh": True}
    assert result["github_username"] == "MiXeDCase"
    assert result["id"] == "saved-passport"
    assert result["level_changes"] == [
        {"skill_id": "python", "from": "Applied", "to": "Demonstrated"}
    ]
    assert saved == [result]


def test_recheck_preserves_legacy_resume_and_certificate_claims(monkeypatch) -> None:
    existing = {
        "id": "saved-passport",
        "github_username": "student",
        "skills": [
            {"skill_id": "python", "name": "Python", "level": "Claimed"},
            {"skill_id": "aws", "name": "AWS", "level": "Claimed", "certified": True},
        ],
        "sources": {"resume": True, "certificates": 1},
        "role_match": {"role_id": "backend-intern"},
    }
    captured = {}
    monkeypatch.setattr(pipeline, "build_passport", lambda **kwargs: captured.update(kwargs))

    pipeline.recheck_passport(existing)

    assert captured["resume_skill_mentions"] == [{"skill_id": "python", "mention": "Python"}]
    assert captured["certificate_skill_mentions"] == [{"skill_id": "aws", "mention": "AWS"}]


def _stub_pipeline(monkeypatch, existing, new_skills, growth=None):
    saved: list[dict] = []
    monkeypatch.setattr(pipeline, "get_passport", lambda _: existing)
    monkeypatch.setattr(
        pipeline, "ingest_github",
        lambda username, force_refresh=False: {"username": username, "repo_count": 1, "repos": []},
    )
    monkeypatch.setattr(pipeline, "propose_claims", lambda *_: [])
    monkeypatch.setattr(
        pipeline, "verify_claims",
        lambda *_a, **_k: {"skills": new_skills, "rejected_claims": []},
    )
    monkeypatch.setattr(pipeline, "add_copy_signals", lambda *_a, **_k: None)
    monkeypatch.setattr(
        pipeline, "match_role", lambda *_: {"role_id": "backend-intern", "role_name": "Backend Intern"},
    )
    monkeypatch.setattr(
        pipeline, "generate_growth_and_kit",
        growth or (lambda *_: {"growth_tasks": [], "interview_kit": []}),
    )
    monkeypatch.setattr(pipeline, "save_passport", saved.append)
    return saved


EXISTING = {
    "id": "p1", "created_at": "2026-10-01T00:00:00+00:00",
    "skills": [{"skill_id": "python", "level": "Applied"}],
    "sources": {"resume": True, "certificates": 0},
    "role_match": {"role_id": "backend-intern"}, "is_demo": True,
}


def _recheck():
    return pipeline.build_passport(
        github_username="student", target_role="backend-intern", resume_bytes=b"",
        passport_id="p1", force_refresh=True, resume_skill_mentions=[],
    )


def test_recheck_reports_skills_that_appear_for_the_first_time(monkeypatch) -> None:
    _stub_pipeline(monkeypatch, EXISTING, [
        {"skill_id": "python", "level": "Applied", "evidence": []},
        {"skill_id": "docker", "level": "Used", "evidence": [{"type": "config"}]},
    ])
    result = _recheck()
    assert result["level_changes"] == [{"skill_id": "docker", "from": "Claimed", "to": "Used"}]
    assert result["created_at"] == EXISTING["created_at"]
    assert result["updated_at"] != EXISTING["created_at"]


def test_recheck_reports_new_skills_when_the_old_passport_had_none(monkeypatch) -> None:
    _stub_pipeline(monkeypatch, {**EXISTING, "skills": []}, [
        {"skill_id": "docker", "level": "Used", "evidence": [{"type": "config"}]},
    ])
    assert _recheck()["level_changes"] == [{"skill_id": "docker", "from": "Claimed", "to": "Used"}]


def test_recheck_preserves_previous_skill_without_fresh_support_as_claimed(monkeypatch) -> None:
    previous = {
        **EXISTING,
        "skills": [{
            "skill_id": "python", "name": "Python", "level": "Applied",
            "certified": False, "evidence": [{"type": "code"}],
        }],
    }
    saved = _stub_pipeline(monkeypatch, previous, [])
    from app.services.role_match import match_role

    monkeypatch.setattr(pipeline, "match_role", match_role)

    result = _recheck()

    assert result["skills"] == [{
        "skill_id": "python", "name": "Python", "level": "Claimed",
        "certified": False, "evidence": [],
    }]
    assert result["level_changes"] == [
        {"skill_id": "python", "from": "Applied", "to": "Claimed"}
    ]
    python_requirement = next(
        item for item in result["role_match"]["requirements"] if item["skill_id"] == "python"
    )
    assert python_requirement["status"] == "missing"
    assert python_requirement["student_level"] == "Claimed"


def test_level_changes_are_computed_after_copy_exclusion(monkeypatch) -> None:
    copied = {
        "type": "code", "repo": "demo", "file_path": "a.py", "start_line": 1, "end_line": 2,
        "integrity": [{"id": "copy_match", "status": "warn", "earlier_source": True,
                       "source_repo": "o/r", "source_url": "u", "source_first_seen": "2020-01-01",
                       "candidate_first_seen": "2024-01-01", "detail": "Matches code that appeared in o/r"}],
    }
    _stub_pipeline(monkeypatch, {**EXISTING, "skills": [{"skill_id": "python", "level": "Applied"}]}, [
        {"skill_id": "python", "level": "Applied", "evidence": [copied], "resume_mention": "Python"},
    ])
    assert _recheck()["level_changes"] == [{"skill_id": "python", "from": "Applied", "to": "Claimed"}]


def test_growth_generation_failure_still_saves_the_verified_passport(monkeypatch) -> None:
    def broken(*_):
        raise RuntimeError("quota exceeded")

    saved = _stub_pipeline(
        monkeypatch, EXISTING, [{"skill_id": "python", "level": "Applied", "evidence": []}], broken,
    )
    result = _recheck()
    assert saved and result["growth_tasks"] == [] and result["interview_kit"] == []
    assert "Re-check" in result["generation_warning"]


def test_growth_generation_error_is_stored_and_passport_saved(monkeypatch) -> None:
    saved = _stub_pipeline(
        monkeypatch, EXISTING, [{"skill_id": "python", "level": "Applied", "evidence": []}],
        lambda *_: {"growth_tasks": [], "interview_kit": [], "generation_error": "Invalid growth data"},
    )
    result = _recheck()
    assert saved == [result]
    assert result["generation_error"] == "Invalid growth data"
    assert result["growth_tasks"] == []

    import app.main as main_module

    assert main_module.student_view(result)["generation_error"] == "Invalid growth data"


def test_gemini_requests_use_temperature_zero(monkeypatch) -> None:
    from app.services import gemini

    configs = []

    class Models:
        def generate_content(self, **kwargs):
            configs.append(kwargs["config"])
            return type("R", (), {"text": "{}"})()

    gemini.generate_json(type("C", (), {"models": Models()})(), "prompt")
    assert configs[0].temperature == 0


def test_recheck_all_prints_one_line_per_passport_and_survives_failures(monkeypatch, capsys) -> None:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "recheck_all.py"
    spec = importlib.util.spec_from_file_location("recheck_all", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    stored = [
        {"id": "a", "github_username": "alice", "role_match": {"met": 2, "total": 8}},
        {"id": "b", "github_username": "bob", "role_match": {"met": 5, "total": 8}},
    ]

    def rebuild(existing):
        if existing["id"] == "b":
            raise RuntimeError("GitHub API rate limit exceeded")
        return {"github_username": "alice", "role_match": {"met": 4, "total": 8}, "rejected_claims": [
            {"kind": "unsupported"}, {"kind": "copied"}, {"kind": "copied"},
        ]}

    monkeypatch.setattr(script, "list_passports", lambda: stored)
    monkeypatch.setattr(script, "recheck_passport", rebuild)
    assert script.main([]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[1].split() == ["alice", "met", "2", "->", "4", "of", "8", "rejected", "1", "excluded", "2"]
    assert out[2].startswith("bob") and "FAILED" in out[2]
    assert out[-1] == "Done: 1 rebuilt, 1 failed."


def test_recheck_all_only_incomplete_rebuilds_just_what_needs_it(monkeypatch, capsys) -> None:
    import importlib.util

    from app.config import VERIFIER_VERSION

    path = Path(__file__).resolve().parents[1] / "scripts" / "recheck_all.py"
    spec = importlib.util.spec_from_file_location("recheck_all", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    complete = {"coverage": {"repos": []}, "growth_tasks": [{"skill_id": "sql"}], "role_match": {"met": 2, "total": 8},
                "verifier_version": VERIFIER_VERSION}
    record = {"type": "code", "record": {"verifier_version": "2026-10-15"}}
    stored = [
        {**complete, "id": "ok", "github_username": "fine"},
        {**complete, "id": "a", "github_username": "nocov", "coverage": None},
        {**complete, "id": "b", "github_username": "nogrowth", "growth_tasks": [], "generation_error": "ServerError"},
        {**complete, "id": "c", "github_username": "oldrules", "verifier_version": None,
         "skills": [{"skill_id": "python", "evidence": [record]}]},
        {**complete, "id": "d", "github_username": "unversioned", "verifier_version": None, "skills": []},
        # No gaps and no error: an empty growth plan is fine.
        {**complete, "id": "e", "github_username": "nogaps", "growth_tasks": []},
        {**complete, "id": "f", "github_username": "old-recheck-rules",
         "verifier_version": "2026-10-20"},
    ]
    assert [script.incomplete_reasons(item) for item in stored] == [
        [], ["no coverage"], ["no growth plan"], ["verifier 2026-10-15"],
        ["verifier unversioned"], [], ["verifier 2026-10-20"],
    ]
    rebuilt = []
    monkeypatch.setattr(script, "list_passports", lambda: stored)
    monkeypatch.setattr(script, "recheck_passport", lambda old: rebuilt.append(old["id"]) or {
        **old, "rejected_claims": [{"kind": "not_written_by_candidate"}, {"kind": "earlier_source"}]})
    assert script.main(["--only-incomplete"]) == 0
    assert rebuilt == ["a", "b", "c", "d", "f"]
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Re-checking 5 of 7 passport(s) (incomplete or older rules)..."
    assert "rejected 0  excluded 2" in out[1] and out[1].endswith("[no coverage]")


def test_gemini_busy_errors_are_retried(monkeypatch) -> None:
    from google.genai import errors

    from app.services import gemini

    calls = {"n": 0}

    class Models:
        def generate_content(self, **_):
            calls["n"] += 1
            if calls["n"] < 3:
                raise errors.ServerError(503, {"error": {"message": "busy"}})
            return type("R", (), {"text": '{"claims": []}'})()

    monkeypatch.setattr(gemini.time, "sleep", lambda _: None)
    client = type("C", (), {"models": Models()})()
    assert gemini.generate_json(client, "prompt") == '{"claims": []}'
    assert calls["n"] == 3


def _flaky_client(failures: list[Exception]):
    calls = {"n": 0}

    class Models:
        def generate_content(self, **_):
            calls["n"] += 1
            if failures:
                raise failures.pop(0)
            return type("R", (), {"text": '{"claims": []}'})()

    return type("C", (), {"models": Models()})(), calls


def test_gemini_connection_errors_are_retried_twice(monkeypatch) -> None:
    import httpx
    import pytest

    from app.services import gemini

    delays: list[float] = []
    monkeypatch.setattr(gemini.time, "sleep", delays.append)
    for error in (httpx.RemoteProtocolError("Server disconnected"), httpx.ReadTimeout("timed out"),
                  httpx.ConnectError("refused"), ConnectionResetError("reset by peer")):
        client, calls = _flaky_client([error, error])
        assert gemini.generate_json(client, "prompt") == '{"claims": []}'
        assert calls["n"] == 3, error
    assert delays[:2] == [3, 8]  # back-off
    # A third consecutive failure is raised (2 retries at most).
    client, calls = _flaky_client([httpx.RemoteProtocolError("x")] * 3)
    with pytest.raises(httpx.RemoteProtocolError):
        gemini.generate_json(client, "prompt")
    assert calls["n"] == 3
