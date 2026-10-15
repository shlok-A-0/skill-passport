import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import pipeline


def test_recheck_refreshes_ingestion_without_treating_passport_as_job(monkeypatch) -> None:
    existing = {
        "id": "saved-passport",
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


def test_level_changes_are_computed_after_copy_exclusion(monkeypatch) -> None:
    copied = {
        "type": "code", "repo": "demo", "file_path": "a.py", "start_line": 1, "end_line": 2,
        "integrity": [{"id": "copy_match", "status": "warn", "confirmed_copy": True,
                       "source_repo": "o/r", "source_url": "u", "source_created_at": "2020-01-01"}],
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
    assert script.main() == 1
    out = capsys.readouterr().out.splitlines()
    assert out[1].split() == ["alice", "met", "2", "->", "4", "of", "8", "rejected", "1", "excluded", "2"]
    assert out[2].startswith("bob") and "FAILED" in out[2]
    assert out[-1] == "Done: 1 rebuilt, 1 failed."


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
