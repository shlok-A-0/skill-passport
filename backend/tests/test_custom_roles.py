import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.main as main_module
from app import config
from app.services import jd_parse, pipeline, storage
from app.services.claim_validation import normalise, quote_in_text, validate_proposals
from app.services.gemini import GeminiOutputError, GeminiUnavailableError
from app.services.role_match import match_role

DESCRIPTION = """Backend Engineer Intern
You must have strong Python experience building services.
Write unit tests with pytest for every endpoint.
Experience with Postgres is a plus.
Familiarity with containerised deployments is nice to have.
Knowledge of Rust is preferred."""


def _gemini(monkeypatch, items):
    calls = []

    def propose(prompt, key, cache_prefix):
        calls.append(prompt)
        if isinstance(items, Exception):
            raise items
        return items

    monkeypatch.setattr(jd_parse, "propose_json_items", propose)
    return calls


GEMINI_ITEMS = [
    {"skill_id": "python", "importance": "must", "quote": "strong Python experience"},
    {"skill_id": "unit-testing", "importance": "must", "quote": "Write unit tests with pytest"},
    {"skill_id": "docker", "importance": "nice", "quote": "containerised   deployments"},
    {"skill_id": "rust", "importance": "nice", "quote": "Knowledge of Rust"},
    {"skill_id": "kubernetes", "importance": "must", "quote": "Kubernetes clusters"},
]


def _by_skill(result):
    return {item["skill_id"]: item for item in result["requirements"]}


def test_quote_helpers_normalise_case_and_whitespace() -> None:
    assert normalise("  Strong\n  PYTHON\tskills ") == "strong python skills"
    assert quote_in_text("python   SKILLS", "Strong\nPython skills")
    assert not quote_in_text("   ", "anything")
    assert not quote_in_text("Rust", "Python only")


def test_validate_proposals_drops_unknown_and_unquoted_items_dedupes_and_caps() -> None:
    kept, dropped = validate_proposals(
        [
            {"skill_id": "python", "quote": "Python"},
            {"skill_id": "python", "quote": "python again"},
            {"skill_id": "rust", "quote": "Rust"},
            {"skill_id": "docker", "quote": "Kubernetes"},
            "not a dict",
            {"skill_id": "sql", "quote": "SQL"},
            {"skill_id": "git", "quote": "Git"},
        ],
        "Python, python again, SQL and Git. Rust too.",
        limit=2,
    )
    assert [item["skill_id"] for item in kept] == ["python", "sql"]
    assert kept[0]["quote"] == "Python"  # first one kept by default
    reasons = {(item["skill_id"], item["reason"]) for item in dropped}
    assert ("rust", "Not a skill in the skill framework.") in reasons
    assert ("docker", "Quote not found in the text.") in reasons
    assert (None, "Not a valid proposal.") in reasons
    assert ("git", "Over the limit of 2.") in reasons


def test_exact_alias_match_is_found_without_the_ai(monkeypatch) -> None:
    _gemini(monkeypatch, [])
    result = jd_parse.extract_requirements("Backend Engineer Intern", DESCRIPTION)
    postgres = _by_skill(result)["postgresql"]
    assert postgres["source"] == "exact"
    assert "Postgres" in postgres["quote"] and quote_in_text(postgres["quote"], DESCRIPTION)
    # Gemini gave no importance: no "required/must/essential/strong" in the quote -> nice -> Used.
    assert (postgres["importance"], postgres["min_level"]) == ("nice", "Used")


def test_paraphrase_is_accepted_only_when_its_quote_is_in_the_text(monkeypatch) -> None:
    _gemini(monkeypatch, GEMINI_ITEMS)
    result = jd_parse.extract_requirements("Backend Engineer Intern", DESCRIPTION)
    requirements = _by_skill(result)
    assert requirements["docker"]["source"] == "ai"
    assert requirements["docker"]["quote"] == "containerised   deployments"
    assert "kubernetes" not in requirements
    assert {"skill_id": "kubernetes", "quote": "Kubernetes clusters",
            "reason": "Quote not found in the text."} in result["dropped"]


def test_unknown_skill_id_is_dropped(monkeypatch) -> None:
    _gemini(monkeypatch, GEMINI_ITEMS)
    result = jd_parse.extract_requirements("Backend Engineer Intern", DESCRIPTION)
    assert "rust" not in _by_skill(result)
    assert {"skill_id": "rust", "quote": "Knowledge of Rust",
            "reason": "Not a skill in the skill framework."} in result["dropped"]


def test_exact_quote_wins_and_ai_sets_importance(monkeypatch) -> None:
    _gemini(monkeypatch, GEMINI_ITEMS)
    requirements = _by_skill(jd_parse.extract_requirements("Backend Engineer Intern", DESCRIPTION))
    python = requirements["python"]
    assert python["source"] == "exact"
    assert python["quote"] != "strong Python experience"  # the deterministic quote is kept
    assert "Python" in python["quote"]
    assert (python["importance"], python["min_level"]) == ("must", "Applied")
    testing = requirements["unit-testing"]
    assert (testing["importance"], testing["min_level"]) == ("must", "Demonstrated")


MUST_ALL = [
    {"skill_id": skill_id, "importance": "must", "quote": "x"}  # quotes unused: exact matches win
    for skill_id in ("python", "fastapi", "sql", "rest-api", "unit-testing")
]


def test_one_line_quotes_stop_at_sentence_ends_so_testing_words_do_not_leak(monkeypatch) -> None:
    _gemini(monkeypatch, MUST_ALL)
    description = (
        "You must have strong Python skills and experience building REST APIs with FastAPI. "
        "Solid SQL is required; write unit tests with pytest! Docker is a plus?"
    )
    requirements = _by_skill(jd_parse.extract_requirements("Backend Intern", description))
    # Ends at its own sentence; the start is trimmed by the 60-character context limit.
    assert requirements["fastapi"]["quote"] == "strong Python skills and experience building REST APIs with FastAPI."
    assert requirements["sql"]["quote"] == "Solid SQL is required;"
    for skill_id in ("python", "fastapi", "sql"):
        assert "test" not in requirements[skill_id]["quote"].lower(), skill_id
        assert requirements[skill_id]["min_level"] == "Applied", skill_id
    assert requirements["unit-testing"]["quote"] == "write unit tests with pytest!"
    assert requirements["unit-testing"]["min_level"] == "Demonstrated"


def test_bulleted_quotes_stay_on_their_own_line(monkeypatch) -> None:
    _gemini(monkeypatch, MUST_ALL)
    description = (
        "Requirements:\n"
        "- Strong Python and FastAPI experience (required)\n"
        "- Solid SQL is required\n"
        "- Unit tests with pytest are required\n"
        "- Docker is a plus"
    )
    requirements = _by_skill(jd_parse.extract_requirements("Backend Intern", description))
    assert requirements["fastapi"]["quote"] == "- Strong Python and FastAPI experience (required)"
    assert requirements["sql"]["quote"] == "- Solid SQL is required"
    assert requirements["fastapi"]["min_level"] == "Applied"
    assert requirements["sql"]["min_level"] == "Applied"
    assert requirements["unit-testing"]["min_level"] == "Demonstrated"
    assert all("\n" not in item["quote"] for item in requirements.values())


def test_quotes_are_not_cut_inside_names_or_version_numbers() -> None:
    line = "We use Node.js 20 and Python 3.11 daily. Kubernetes is a plus."
    node = line.index("Node.js")
    assert jd_parse._quote_around(line, (node, node + len("Node.js"))) == "We use Node.js 20 and Python 3.11 daily."
    long_line = "word " * 30 + "Python" + " more" * 30
    python = long_line.index("Python")
    quote = jd_parse._quote_around(long_line, (python, python + 6))
    assert "Python" in quote and len(quote) <= 6 + 2 * jd_parse.QUOTE_CONTEXT_CHARS
    assert not quote.startswith("ord") and not quote.endswith("mor")  # cut at word boundaries


def test_min_level_rules_are_fixed_code() -> None:
    assert jd_parse.min_level_for("nice", "unit tests with pytest") == "Used"
    assert jd_parse.min_level_for("must", "Python services") == "Applied"
    for word in ("test", "tests", "tested", "testing", "unit test", "TDD", "pytest", "Jest"):
        assert jd_parse.min_level_for("must", f"Python, {word} daily") == "Demonstrated", word
    assert jd_parse.min_level_for("must", "attestation of Python") == "Applied"  # whole words only
    assert jd_parse.default_importance("Python is required") == "must"
    assert jd_parse.default_importance("Strong SQL") == "must"
    assert jd_parse.default_importance("Docker is a plus") == "nice"


def test_ai_cannot_set_the_minimum_level(monkeypatch) -> None:
    _gemini(monkeypatch, [{"skill_id": "docker", "importance": "nice", "quote": "containerised deployments",
                           "min_level": "Demonstrated"}, *GEMINI_ITEMS[:2]])
    docker = _by_skill(jd_parse.extract_requirements("Backend Engineer Intern", DESCRIPTION))["docker"]
    assert docker["min_level"] == "Used"


def test_dedupe_keeps_the_stricter_requirement(monkeypatch) -> None:
    _gemini(monkeypatch, [
        *GEMINI_ITEMS[:2],
        {"skill_id": "docker", "importance": "nice", "quote": "containerised deployments"},
        {"skill_id": "docker", "importance": "must", "quote": "Familiarity with containerised deployments"},
    ])
    docker = _by_skill(jd_parse.extract_requirements("Backend Engineer Intern", DESCRIPTION))["docker"]
    assert (docker["importance"], docker["min_level"]) == ("must", "Applied")
    assert docker["quote"] == "Familiarity with containerised deployments"


def test_requirements_are_capped_at_twelve_must_haves_first(monkeypatch) -> None:
    names = ["Python", "Java", "JavaScript", "TypeScript", "Golang", "Flask", "Django", "FastAPI",
             "React", "SQL", "MySQL", "MongoDB", "Redis", "Docker", "Kubernetes"]
    description = "Required: Python.\n" + "\n".join(f"Bonus: {name} is a plus." for name in names[1:])
    _gemini(monkeypatch, [{"skill_id": "python", "importance": "must", "quote": "Required: Python."}])
    result = jd_parse.extract_requirements("Generalist", description)
    assert len(result["requirements"]) == 12
    assert result["requirements"][0]["skill_id"] == "python"
    assert sum(1 for item in result["dropped"] if item["reason"] == "Over the limit of 12.") == 3


def test_at_least_one_must_requirement_is_needed(monkeypatch) -> None:
    _gemini(monkeypatch, [])
    with pytest.raises(jd_parse.JobDescriptionError, match="must-have"):
        jd_parse.extract_requirements("Intern", "Docker is a plus. Postgres would be nice. " * 3)
    with pytest.raises(jd_parse.JobDescriptionError, match="No skills"):
        jd_parse.extract_requirements("Intern", "We want a friendly person who enjoys learning things. " * 2)


def test_input_lengths_are_checked() -> None:
    with pytest.raises(jd_parse.JobDescriptionError, match="title"):
        jd_parse.validate_input("  ", DESCRIPTION)
    with pytest.raises(jd_parse.JobDescriptionError, match="title"):
        jd_parse.validate_input("x" * 121, DESCRIPTION)
    with pytest.raises(jd_parse.JobDescriptionError, match="50-8000"):
        jd_parse.validate_input("Intern", "Python required.")
    with pytest.raises(jd_parse.JobDescriptionError, match="50-8000"):
        jd_parse.validate_input("Intern", "Python " * 1200)


def test_invalid_ai_output_falls_back_to_exact_matches_with_default_importance(monkeypatch) -> None:
    _gemini(monkeypatch, GeminiOutputError("not json"))
    result = jd_parse.extract_requirements("Backend Engineer Intern", DESCRIPTION)
    requirements = _by_skill(result)
    assert set(requirements) == {"python", "unit-testing", "postgresql"}
    assert requirements["python"]["importance"] == "must"  # "must ... strong" in the quote
    assert result["mode"] == "exact_only" and result["note"] == jd_parse.EXACT_ONLY_NOTE


def test_contact_details_are_removed_before_the_ai_call(monkeypatch) -> None:
    calls = _gemini(monkeypatch, [])
    jd_parse.extract_requirements("Intern", DESCRIPTION + "\nApply: jobs@example.com or +1 415 555 0100.")
    assert "jobs@example.com" not in calls[0] and "555 0100" not in calls[0]


def test_gemini_helper_needs_a_key_retries_invalid_json_once_and_caches(tmp_path, monkeypatch) -> None:
    from app.services import gemini

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with pytest.raises(GeminiUnavailableError):
        gemini.propose_json_items("prompt", "requirements", "jd")

    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemini.genai, "Client", lambda api_key: object())
    answers = ["not json", '{"requirements": [{"skill_id": "python"}]}']
    monkeypatch.setattr(gemini, "generate_json", lambda client, contents: answers.pop(0))
    assert gemini.propose_json_items("prompt", "requirements", "jd") == [{"skill_id": "python"}]
    assert gemini.propose_json_items("prompt", "requirements", "jd") == [{"skill_id": "python"}]  # cached

    monkeypatch.setattr(gemini, "generate_json", lambda client, contents: "[]")
    with pytest.raises(GeminiOutputError):
        gemini.propose_json_items("other prompt", "requirements", "jd")

    def offline(client, contents):
        raise ConnectionError("offline")

    monkeypatch.setattr(gemini, "generate_json", offline)
    with pytest.raises(GeminiUnavailableError):
        gemini.propose_json_items("third prompt", "requirements", "jd")


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(config, "ENABLE_CUSTOM_ROLES", True)
    return TestClient(main_module.app)


def test_endpoint_stores_the_role_without_the_description(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _gemini(monkeypatch, GEMINI_ITEMS)
    response = client.post("/roles/from-description", json={"title": "Backend Engineer Intern", "description": DESCRIPTION})
    assert response.status_code == 201
    role = response.json()
    assert role["id"].startswith("custom-") and role["name"] == "Backend Engineer Intern"
    assert storage.get_custom_role(role["id"])["requirements"] == role["requirements"]
    with sqlite3.connect(tmp_path / "test.sqlite") as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(custom_roles)")]
        stored = repr(connection.execute("SELECT * FROM custom_roles").fetchall())
    assert columns == ["id", "title", "requirements", "dropped", "created_at"]
    assert "is nice to have" not in stored  # description text that no quote uses


def test_endpoint_errors_are_clear(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _gemini(monkeypatch, [])
    short = client.post("/roles/from-description", json={"title": "Intern", "description": "Python required."})
    assert short.status_code == 422 and "50-8000" in short.json()["detail"]
    assert client.post("/roles/from-description", json={"title": "Intern", "description": DESCRIPTION}).json()["mode"] == "ai"


BULLETED = (
    "Requirements:\n"
    "- Strong Python experience\n"
    "- FastAPI for REST services is required\n"
    "- Solid SQL is essential\n"
    "- Unit tests with pytest are a must\n"
    "- Docker is a plus\n"
    "- Familiarity with containerised deployments"
)
EXPECTED_EXACT_ONLY = {
    "python": ("must", "Applied", "- Strong Python experience"),
    "fastapi": ("must", "Applied", "- FastAPI for REST services is required"),
    "sql": ("must", "Applied", "- Solid SQL is essential"),
    "unit-testing": ("must", "Demonstrated", "- Unit tests with pytest are a must"),
    "docker": ("nice", "Used", "- Docker is a plus"),
}


def _real_gemini_failing(tmp_path, monkeypatch, error: Exception) -> list[int]:
    """The real propose_json_items + generate_json chain, with the Gemini SDK raising `error`."""
    from app.services import gemini

    calls: list[int] = []

    class Models:
        def generate_content(self, **_kwargs):
            calls.append(1)
            raise error

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemini.genai, "Client", lambda api_key: type("C", (), {"models": Models()})())
    monkeypatch.setattr(gemini.time, "sleep", lambda _seconds: None)
    return calls


def _gemini_errors():
    import httpx
    from google.genai import errors

    return [
        errors.ServerError(503, {"error": {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}}),
        httpx.ReadTimeout("timed out"),
        errors.ClientError(429, {"error": {"code": 429, "message": "Quota exceeded.", "status": "RESOURCE_EXHAUSTED"}}),
    ]


@pytest.mark.parametrize("error", _gemini_errors(), ids=["503-overloaded", "timeout", "429-quota"])
def test_unavailable_ai_falls_back_to_exact_matching(tmp_path, monkeypatch, error) -> None:
    calls = _real_gemini_failing(tmp_path, monkeypatch, error)
    result = jd_parse.extract_requirements("Backend Intern", BULLETED)
    assert len(calls) > 1  # the existing retries ran before falling back
    assert result["mode"] == "exact_only" and result["note"] == jd_parse.EXACT_ONLY_NOTE
    assert {item["skill_id"]: (item["importance"], item["min_level"], item["quote"])
            for item in result["requirements"]} == EXPECTED_EXACT_ONLY
    assert all(item["source"] == "exact" for item in result["requirements"])


@pytest.mark.parametrize("error", _gemini_errors()[:2], ids=["503-overloaded", "timeout"])
def test_endpoint_returns_201_with_the_exact_only_flag(tmp_path, monkeypatch, error) -> None:
    client = _client(tmp_path, monkeypatch)
    _real_gemini_failing(tmp_path, monkeypatch, error)
    response = client.post("/roles/from-description", json={"title": "Backend Intern", "description": BULLETED})
    assert response.status_code == 201
    role = response.json()
    assert role["mode"] == "exact_only" and role["note"] == jd_parse.EXACT_ONLY_NOTE
    assert {item["skill_id"] for item in role["requirements"]} == set(EXPECTED_EXACT_ONLY)
    assert storage.get_custom_role(role["id"])["title"] == "Backend Intern"


def test_exact_only_without_a_must_have_is_still_a_clear_error(tmp_path, monkeypatch) -> None:
    import httpx

    client = _client(tmp_path, monkeypatch)
    _real_gemini_failing(tmp_path, monkeypatch, httpx.ReadTimeout("timed out"))
    response = client.post("/roles/from-description", json={
        "title": "Intern", "description": "- Docker is a plus\n- Postgres would be nice\n- Some Redis experience helps"})
    assert response.status_code == 422 and "must-have" in response.json()["detail"]


def test_endpoint_is_rate_limited(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _gemini(monkeypatch, GEMINI_ITEMS)
    statuses = [
        client.post("/roles/from-description", json={"title": "Intern", "description": DESCRIPTION}).status_code
        for _ in range(11)
    ]
    assert statuses[:10] == [201] * 10 and statuses[10] == 429


def _custom_role(tmp_path, monkeypatch, client) -> dict:
    _gemini(monkeypatch, GEMINI_ITEMS)
    return client.post("/roles/from-description", json={"title": "Backend Engineer Intern", "description": DESCRIPTION}).json()


def test_create_passport_with_a_custom_role_passes_the_role_object(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    role = _custom_role(tmp_path, monkeypatch, client)
    received = []
    monkeypatch.setattr(main_module, "run_passport_job", lambda *args, **kwargs: received.append(args[2]))
    response = client.post(
        "/passport/create",
        data={"github_username": "demo-user", "target_role": role["id"], "is_demo": "true"},
        files={"resume": ("resume.pdf", b"%PDF-1.4 resume", "application/pdf")},
    )
    assert response.status_code == 202
    assert received[0]["id"] == role["id"] and received[0]["name"] == "Backend Engineer Intern"
    assert [item["quote"] for item in received[0]["requirements"]] == [item["quote"] for item in role["requirements"]]

    unknown = client.post(
        "/passport/create",
        data={"github_username": "other-user", "target_role": "custom-does-not-exist", "is_demo": "true"},
        files={"resume": ("resume.pdf", b"%PDF-1.4 resume", "application/pdf")},
    )
    assert unknown.status_code == 422 and unknown.json()["detail"] == "Unknown target role."


def test_match_role_uses_the_custom_requirements(tmp_path, monkeypatch) -> None:
    role = jd_parse.role_definition(_custom_role(tmp_path, monkeypatch, _client(tmp_path, monkeypatch)))
    skills = [
        {"skill_id": "python", "level": "Applied", "evidence": [{"type": "code"}]},
        {"skill_id": "unit-testing", "level": "Applied", "evidence": [{"type": "code"}]},
        {"skill_id": "docker", "level": "Claimed", "evidence": []},
    ]
    result = match_role(skills, role)
    statuses = {item["skill_id"]: (item["status"], item["min_level"]) for item in result["requirements"]}
    assert result["role_id"] == role["id"] and result["role_name"] == "Backend Engineer Intern"
    assert statuses["python"] == ("met", "Applied")
    assert statuses["unit-testing"] == ("partial", "Demonstrated")
    assert statuses["docker"] == ("missing", "Used")  # Claimed never satisfies a requirement


def test_fixed_roles_still_match_exactly_as_before() -> None:
    result = match_role([{"skill_id": "python", "level": "Applied", "evidence": [{"type": "code"}]}], "backend-intern")
    assert result["role_name"] == "Backend Developer Intern" and result["total"] == 8


def test_build_passport_stores_the_custom_role_and_recheck_reuses_it(monkeypatch) -> None:
    role = {"id": "custom-1", "name": "Data Intern", "title": "Data Intern", "custom": True,
            "requirements": [{"skill_id": "python", "name": "Python", "importance": "must",
                              "min_level": "Applied", "quote": "strong Python"}]}
    saved: list[dict] = []
    monkeypatch.setattr(pipeline, "get_passport", lambda _: None)
    monkeypatch.setattr(pipeline, "ingest_github", lambda username, force_refresh=False: {"username": username, "repo_count": 0, "repos": []})
    monkeypatch.setattr(pipeline, "propose_claims", lambda *_: [])
    monkeypatch.setattr(pipeline, "verify_claims", lambda *_a, **_k: {"skills": [
        {"skill_id": "python", "level": "Applied", "evidence": [{"type": "code"}]}], "rejected_claims": []})
    monkeypatch.setattr(pipeline, "add_copy_signals", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "exclude_uncounted_citations", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "generate_growth_and_kit", lambda *_: {"growth_tasks": [], "interview_kit": []})
    monkeypatch.setattr(pipeline, "save_passport", saved.append)
    passport = pipeline.build_passport(
        github_username="student", target_role=role, resume_bytes=b"", resume_skill_mentions=[], is_demo=True,
    )
    assert passport["custom_role"] == role
    assert passport["target_role"] == "Data Intern"
    assert passport["role_match"]["role_id"] == "custom-1" and passport["role_match"]["met"] == 1

    captured = {}
    monkeypatch.setattr(pipeline, "build_passport", lambda **kwargs: captured.update(kwargs))
    pipeline.recheck_passport({**passport, "resume_skill_mentions": []})
    assert captured["target_role"] == role
    pipeline.recheck_passport({"id": "p", "github_username": "s", "role_match": {"role_id": "backend-intern"}})
    assert captured["target_role"] == "backend-intern"


def test_flag_off_hides_the_feature(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    role = _custom_role(tmp_path, monkeypatch, client)
    assert client.get("/features").json()["custom_roles"] is True
    monkeypatch.setattr(config, "ENABLE_CUSTOM_ROLES", False)
    assert client.get("/features").json()["custom_roles"] is False
    off = client.post("/roles/from-description", json={"title": "Intern", "description": DESCRIPTION})
    assert off.status_code == 404
    monkeypatch.setattr(main_module, "run_passport_job", lambda *args, **kwargs: None)
    create = client.post(
        "/passport/create",
        data={"github_username": "demo-user", "target_role": role["id"], "is_demo": "true"},
        files={"resume": ("resume.pdf", b"%PDF-1.4 resume", "application/pdf")},
    )
    assert create.status_code == 422
