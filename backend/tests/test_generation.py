import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import claim_proposer, growth_and_kit


def _claim(skill_id: str, start: int = 1) -> dict:
    return {
        "skill_id": skill_id, "repo": "demo", "file_path": "app.py",
        "start_line": start, "end_line": start + 1, "explanation": "Uses it.",
    }


def _fake_gemini(monkeypatch, module, responses: list[str]) -> list[str]:
    """Patch a module's Gemini client and cache; returns the prompts it was sent."""
    sent: list[str] = []
    replies = iter(responses)
    monkeypatch.setattr(module, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(module.genai, "Client", lambda api_key: object())

    def generate(client, contents):
        sent.append(contents)
        return next(replies)

    monkeypatch.setattr(module, "generate_json", generate)
    return sent


def test_unknown_skill_id_is_dropped_not_fatal(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(claim_proposer, "CACHE_DIR", tmp_path)
    reply = json.dumps({"claims": [_claim("made-up-skill"), _claim("flask")]})
    sent = _fake_gemini(monkeypatch, claim_proposer, [reply])
    claims = claim_proposer.propose_claims({"repos": []}, [])
    assert [item["skill_id"] for item in claims] == ["flask"]
    assert len(sent) == 1  # no retry needed


def test_five_citations_for_one_skill_are_trimmed_to_three() -> None:
    reply = json.dumps({"claims": [_claim("python", start) for start in range(1, 6)]})
    proposal = claim_proposer._lenient_proposal(reply)
    assert [claim.start_line for claim in proposal.claims] == [1, 2, 3]


def _growth_inputs() -> tuple[list, dict, dict]:
    skills = [{"skill_id": "flask", "level": "Applied", "evidence": [{
        "type": "code", "repo": "demo", "file_path": "app.py", "start_line": 1, "end_line": 2,
        "snippet": "from flask import Flask\napp = Flask(__name__)",
    }]}]
    role_match = {"role_name": "Backend", "requirements": [
        {"skill_id": "docker", "status": "missing"}, {"skill_id": "sql", "status": "partial"},
        {"skill_id": "flask", "status": "met"},
    ]}
    ingestion = {"repos": [{"name": "demo", "source_files": {}}]}
    return skills, role_match, ingestion


def _task(skill_id: str, repo: str) -> dict:
    return {"skill_id": skill_id, "target_repo": repo, "task": "Add the missing proof to the repo.",
            "evidence_it_will_create": "A file that the checks can verify."}


def _kit(skill_id: str) -> dict:
    question = {"q": "Why does app.py lines 1-2 work?", "good_answer_mentions": "The app factory."}
    return {"skill_id": skill_id, "questions": [question, question]}


def test_growth_output_with_an_invalid_task_keeps_the_valid_ones(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(growth_and_kit, "CACHE_DIR", tmp_path)
    reply = json.dumps({
        "growth_tasks": [_task("docker", "demo"), _task("sql", "invented-repo"), _task("kubernetes", "demo")],
        "interview_kit": [_kit("react"), _kit("flask")],
    })
    sent = _fake_gemini(monkeypatch, growth_and_kit, [reply])
    result = growth_and_kit.generate_growth_and_kit(*_growth_inputs())
    assert [task["skill_id"] for task in result["growth_tasks"]] == ["docker"]
    assert [entry["skill_id"] for entry in result["interview_kit"]] == ["flask"]
    assert "generation_error" not in result
    assert len(sent) == 1


def test_growth_failure_after_retry_returns_empty_lists_and_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(growth_and_kit, "CACHE_DIR", tmp_path)
    secret = "SECRET-GOOD-ANSWER"
    bad = json.dumps({"growth_tasks": [_task("docker", "demo")], "interview_kit": [
        {"skill_id": "flask", "questions": [{"q": "Only one question here?", "good_answer_mentions": secret}]},
    ]})
    sent = _fake_gemini(monkeypatch, growth_and_kit, ["not json at all", bad])
    result = growth_and_kit.generate_growth_and_kit(*_growth_inputs())
    assert result["growth_tasks"] == [] and result["interview_kit"] == []
    assert result["generation_error"] and secret not in result["generation_error"]
    assert len(sent) == 2


def test_malformed_growth_cache_is_removed_and_regenerated(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(growth_and_kit, "CACHE_DIR", tmp_path)
    skills, role_match, ingestion = _growth_inputs()
    prompt, _, _ = growth_and_kit._build_prompt(skills, role_match, ingestion)
    cache_key = hashlib.sha256(
        f"{growth_and_kit.GEMINI_MODEL}\0{prompt}".encode("utf-8")
    ).hexdigest()
    cache_path = tmp_path / f"growth-{cache_key}.json"
    cache_path.write_text("{broken json", encoding="utf-8")
    reply = json.dumps({
        "growth_tasks": [_task("docker", "demo")],
        "interview_kit": [_kit("flask")],
    })
    sent = _fake_gemini(monkeypatch, growth_and_kit, [reply])

    def generate(_client, contents):
        assert not cache_path.exists()
        sent.append(contents)
        return reply

    monkeypatch.setattr(growth_and_kit, "generate_json", generate)

    result = growth_and_kit.generate_growth_and_kit(skills, role_match, ingestion)

    assert result["growth_tasks"][0]["skill_id"] == "docker"
    assert json.loads(cache_path.read_text(encoding="utf-8")) == result
    assert len(sent) == 1


def _code_evidence(start: int, end: int, line_share: str | None = None) -> dict:
    integrity = [{"id": "line_share", "status": line_share}] if line_share else []
    return {"type": "code", "repo": "demo", "file_path": f"f{start}.py", "start_line": start,
            "end_line": end, "snippet": "code", "integrity": integrity}


def test_interview_kit_picks_the_strongest_skills_first() -> None:
    skills = [
        {"skill_id": "django", "level": "Applied", "evidence": [_code_evidence(1, 5)]},
        {"skill_id": "flask", "level": "Applied", "evidence": [_code_evidence(1, 5), _code_evidence(9, 12)]},
        {"skill_id": "git", "level": "Used", "evidence": [{"type": "config"}]},
        {"skill_id": "python", "level": "Demonstrated", "evidence": [_code_evidence(1, 5)]},
    ]
    context = growth_and_kit._interview_context(skills)
    assert [item["skill_id"] for item in context] == ["python", "flask", "django"]


def test_interview_kit_prefers_the_students_own_lines_over_a_longer_citation() -> None:
    skills = [{"skill_id": "flask", "level": "Applied", "evidence": [
        _code_evidence(1, 80, line_share="warn"),
        _code_evidence(100, 110, line_share="ok"),
        _code_evidence(200, 205),
    ]}]
    assert growth_and_kit._interview_context(skills)[0]["file"] == "demo/f100.py#L100-L110"
    # Without an "ok" line share, the longest range wins.
    skills[0]["evidence"] = [_code_evidence(1, 3), _code_evidence(10, 40)]
    assert growth_and_kit._interview_context(skills)[0]["file"] == "demo/f10.py#L10-L40"


def test_interview_prompt_asks_for_repo_specific_questions() -> None:
    prompt, _, _ = growth_and_kit._build_prompt(*_growth_inputs())
    assert "design decisions" in prompt and "language-trivia" in prompt


def test_no_usable_growth_task_triggers_the_retry(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(growth_and_kit, "CACHE_DIR", tmp_path)
    only_invalid = json.dumps({"growth_tasks": [_task("docker", "invented-repo")], "interview_kit": []})
    valid = json.dumps({"growth_tasks": [_task("sql", "demo")], "interview_kit": [_kit("flask")]})
    sent = _fake_gemini(monkeypatch, growth_and_kit, [only_invalid, valid])
    result = growth_and_kit.generate_growth_and_kit(*_growth_inputs())
    assert [task["skill_id"] for task in result["growth_tasks"]] == ["sql"]
    assert len(sent) == 2
