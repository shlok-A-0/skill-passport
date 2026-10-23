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


def test_exact_matches_take_quote_and_importance_from_the_rule_not_the_ai(monkeypatch) -> None:
    _gemini(monkeypatch, GEMINI_ITEMS)
    requirements = _by_skill(jd_parse.extract_requirements("Backend Engineer Intern", DESCRIPTION))
    python = requirements["python"]
    assert python["source"] == "exact"
    assert python["quote"] != "strong Python experience"  # the deterministic quote is kept
    assert "Python" in python["quote"]
    assert (python["importance"], python["min_level"]) == ("must", "Applied")
    # Gemini said "must", but the line has no must-word and no section heading: the rule says nice.
    testing = requirements["unit-testing"]
    assert (testing["importance"], testing["min_level"], testing["source"]) == ("nice", "Used", "exact")


MUST_ALL = [
    {"skill_id": skill_id, "importance": "must", "quote": "x"}  # quotes unused: exact matches win
    for skill_id in ("python", "fastapi", "sql", "rest-api", "unit-testing")
]


def test_one_line_quotes_stop_at_sentence_ends_so_testing_words_do_not_leak(monkeypatch) -> None:
    _gemini(monkeypatch, MUST_ALL)
    description = (
        "You must have strong Python skills and experience building REST APIs with FastAPI. "
        "Solid SQL is required; unit tests with pytest are a must! Docker is a plus?"
    )
    requirements = _by_skill(jd_parse.extract_requirements("Backend Intern", description))
    # Ends at its own sentence; the start is trimmed by the 60-character context limit.
    assert requirements["fastapi"]["quote"] == "strong Python skills and experience building REST APIs with FastAPI."
    assert requirements["sql"]["quote"] == "Solid SQL is required;"
    for skill_id in ("python", "fastapi", "sql"):
        assert "test" not in requirements[skill_id]["quote"].lower(), skill_id
        assert requirements[skill_id]["min_level"] == "Applied", skill_id
    assert requirements["unit-testing"]["quote"] == "unit tests with pytest are a must!"
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
    monkeypatch.setattr(gemini, "generate_json", lambda client, contents, model=None: answers.pop(0))
    assert gemini.propose_json_items("prompt", "requirements", "jd") == [{"skill_id": "python"}]
    assert gemini.propose_json_items("prompt", "requirements", "jd") == [{"skill_id": "python"}]  # cached

    monkeypatch.setattr(gemini, "generate_json", lambda client, contents, model=None: "[]")
    with pytest.raises(GeminiOutputError):
        gemini.propose_json_items("other prompt", "requirements", "jd")

    def offline(client, contents, model=None):
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


NORTHWIND = """About the role
Northwind Labs is hiring a Backend Engineering Intern to join our platform team for six months. You will build and maintain services that expose endpoints for our web and mobile apps.

What you will do
- Build REST APIs with FastAPI for our internal tools.
- Write and optimise SQL queries against our PostgreSQL databases.
- Keep our services reliable: all code is tested, and you will write unit tests with pytest.
- Package services with Docker and keep our GitHub Actions pipelines green.

Requirements
- Strong Python skills are required.
- Solid SQL knowledge is required, including PostgreSQL.
- Experience deploying applications on AWS is required.
- You must be comfortable working with Git and code reviews.

Nice to have
- Familiarity with Docker.
- Experience with CI pipelines such as GitHub Actions.
- Exposure to Kubernetes is a bonus.

What we offer
Mentorship from senior engineers, a real production codebase, and a stipend."""


def _exact_only(monkeypatch, title: str, text: str) -> dict:
    _gemini(monkeypatch, GeminiUnavailableError("Gemini call failed: ServerError"))
    return jd_parse.extract_requirements(title, text)


def test_northwind_exact_only_uses_the_strongest_line_per_skill(monkeypatch) -> None:
    result = _exact_only(monkeypatch, "Backend Engineering Intern", NORTHWIND)
    assert result["mode"] == "exact_only"
    table = {item["skill_id"]: (item["importance"], item["min_level"]) for item in result["requirements"]}
    assert table == {
        "python": ("must", "Applied"),
        "sql": ("must", "Applied"),
        "postgresql": ("must", "Applied"),
        "aws": ("must", "Applied"),
        "fastapi": ("must", "Applied"),
        "rest-api": ("must", "Applied"),
        "git": ("must", "Applied"),
        "unit-testing": ("must", "Demonstrated"),
        "docker": ("nice", "Used"),
        "cicd": ("nice", "Used"),
        "kubernetes": ("nice", "Used"),
    }
    quotes = {item["skill_id"]: item["quote"] for item in result["requirements"]}
    assert quotes["sql"] == "- Solid SQL knowledge is required, including PostgreSQL."  # not the duty line
    assert quotes["docker"] == "- Familiarity with Docker."
    assert quotes["cicd"] == "- Experience with CI pipelines such as GitHub Actions."
    assert quotes["rest-api"] == "- Build REST APIs with FastAPI for our internal tools."
    assert result["dropped"] == []


def test_nice_words_in_the_line_beat_the_section(monkeypatch) -> None:
    text = "Requirements\n- Familiarity with Docker\n- Python is required\n- Exposure to AWS\n- Git"
    table = {item["skill_id"]: (item["importance"], item["quote"])
             for item in _exact_only(monkeypatch, "Intern", text)["requirements"]}
    assert table["docker"] == ("nice", "- Familiarity with Docker")
    assert table["aws"] == ("nice", "- Exposure to AWS")
    assert table["git"] == ("must", "- Git")  # the section alone makes it must
    assert table["python"] == ("must", "- Python is required")


def test_headings_are_recognised_and_other_headings_reset_the_section() -> None:
    assert jd_parse._heading_section("Requirements:") == "requirements"
    assert jd_parse._heading_section("What you'll do") == "responsibilities"
    assert jd_parse._heading_section("Preferred qualifications") == "nice"
    assert jd_parse._heading_section("About the role") is None
    assert jd_parse._heading_section("- Strong Python skills are required.") is False
    matches = jd_parse.strongest_exact_matches("Requirements\n- Docker\nAbout us\n- Redis")
    assert matches["docker"][1] == "must" and matches["redis"][1] == "nice"


def test_plural_aliases_match_in_job_descriptions_only(monkeypatch) -> None:
    from app.services.resume_parse import match_resume_skills

    text = "Requirements\n- You will design RESTful APIs and REST APIs.\n- Strong Python is required."
    skills = {item["skill_id"] for item in _exact_only(monkeypatch, "Intern", text)["requirements"]}
    assert "rest-api" in skills
    assert "rest-api" not in {item["skill_id"] for item in match_resume_skills("Built REST APIs in Python.")}
    # Plain skill-list lines retain the section, including the parser's supported plural aliases.
    plain_list = jd_parse.strongest_exact_matches("Requirements:\nREST APIs\nPython")
    assert plain_list["rest-api"][1] == "must"
    assert plain_list["python"][1] == "must"
    combined_list = jd_parse.strongest_exact_matches("Requirements:\nPython, SQL and Docker")
    assert all(combined_list[skill][1] == "must" for skill in ("python", "sql", "docker"))
    # Everyday-word skills don't get plurals: "reacts" is ordinary English.
    assert "react" not in jd_parse.strongest_exact_matches("The service reacts quickly.\nPython is required.")


def test_ai_mode_cannot_override_exact_matches_and_agrees_with_exact_only(monkeypatch) -> None:
    # Gemini says every named skill is a must-have and adds one paraphrase.
    _gemini(monkeypatch, [
        {"skill_id": "docker", "importance": "must", "quote": "Package services with Docker"},
        {"skill_id": "cicd", "importance": "must", "quote": "GitHub Actions pipelines green"},
        {"skill_id": "kubernetes", "importance": "must", "quote": "Exposure to Kubernetes"},
        {"skill_id": "sql", "importance": "nice", "quote": "Write and optimise SQL queries"},
        {"skill_id": "linux-shell", "importance": "nice", "quote": "maintain services that expose endpoints"},
    ])
    ai = jd_parse.extract_requirements("Backend Engineering Intern", NORTHWIND)
    assert ai["mode"] == "ai"
    table = {item["skill_id"]: (item["importance"], item["min_level"], item["quote"], item["source"])
             for item in ai["requirements"]}
    assert table["docker"][:2] == ("nice", "Used") and table["docker"][2] == "- Familiarity with Docker."
    assert table["cicd"][:2] == ("nice", "Used")
    assert table["kubernetes"][:2] == ("nice", "Used")
    assert table["sql"][:3] == ("must", "Applied", "- Solid SQL knowledge is required, including PostgreSQL.")
    assert table["rest-api"][:2] == ("must", "Applied")
    assert table["linux-shell"] == ("nice", "Used", "maintain services that expose endpoints", "ai")  # paraphrase kept

    exact_only = _exact_only(monkeypatch, "Backend Engineering Intern", NORTHWIND)
    exact = {item["skill_id"]: (item["importance"], item["min_level"], item["quote"]) for item in exact_only["requirements"]}
    assert {skill_id: value[:3] for skill_id, value in table.items() if value[3] == "exact"} == exact


def test_ai_quotes_are_cut_to_one_sentence_and_line(monkeypatch) -> None:
    text = "Requirements\n- Strong Python is required.\n- You will containerise services. Tests are optional\n- Ship often"
    _gemini(monkeypatch, [{"skill_id": "docker", "importance": "must", "quote": "You will containerise services. Tests are optional"}])
    docker = _by_skill(jd_parse.extract_requirements("Intern", text))["docker"]
    assert docker["quote"] == "You will containerise services."
    assert docker["min_level"] == "Applied"  # "Tests" in the next sentence doesn't make it Demonstrated


def test_each_task_uses_its_own_model(tmp_path, monkeypatch) -> None:
    from app.services import gemini

    used: list[str] = []

    class Models:
        def generate_content(self, **kwargs):
            used.append(kwargs["model"])
            return type("R", (), {"text": '{"requirements": []}'})()

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "GEMINI_JD_MODEL", "jd-model")
    monkeypatch.setattr(gemini, "GEMINI_MODEL", "passport-model")
    monkeypatch.setattr(gemini.genai, "Client", lambda api_key: type("C", (), {"models": Models()})())
    gemini.propose_json_items("prompt", "requirements", "jd")
    gemini.generate_json(gemini.genai.Client(api_key="x"), "prompt")
    assert used == ["jd-model", "passport-model"]
    assert config.GEMINI_JD_MODEL and len(list(tmp_path.glob("jd-*.json"))) == 1


def test_exact_only_results_are_never_cached(tmp_path, monkeypatch) -> None:
    from app.services import gemini

    calls = _real_gemini_failing(tmp_path, monkeypatch, gemini.httpx.ReadTimeout("timed out"))
    first = jd_parse.extract_requirements("Backend Engineering Intern", NORTHWIND)
    assert first["mode"] == "exact_only" and calls
    assert list(tmp_path.glob("jd-*.json")) == []  # nothing written for the fallback

    class Working:
        def generate_content(self, **_kwargs):
            calls.append(2)
            return type("R", (), {"text": '{"requirements": [{"skill_id": "python", "importance": "must", "quote": "Strong Python skills are required."}]}'})()

    monkeypatch.setattr(gemini.genai, "Client", lambda api_key: type("C", (), {"models": Working()})())
    second = jd_parse.extract_requirements("Backend Engineering Intern", NORTHWIND)
    assert second["mode"] == "ai" and calls[-1] == 2  # Gemini was asked again, not a reused fallback
    assert len(list(tmp_path.glob("jd-*.json"))) == 1  # only the successful AI answer is cached


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
    # "Write unit tests with pytest..." has no must-word or heading, so the rule makes it nice/Used.
    assert statuses["unit-testing"] == ("met", "Used")
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



def test_skill_mention_inside_heading_does_not_preserve_requirements_section() -> None:
    text = "Requirements:\nPython\nOur Python stack\nRedis"
    matches = jd_parse.strongest_exact_matches(text)
    assert matches["python"][1] == "must"
    assert matches["redis"][1] == "nice"


def test_plain_skill_lines_keep_requirements_section_when_ai_unavailable(monkeypatch) -> None:
    _gemini(monkeypatch, GeminiUnavailableError("offline"))
    description = "Requirements:\nPython\nSQL,\nDocker\n" + ("Build reliable services. " * 3)
    result = jd_parse.extract_requirements("Backend Engineer", description)
    requirements = _by_skill(result)
    assert result["mode"] == "exact_only"
    assert requirements["python"]["importance"] == "must"
    assert requirements["sql"]["importance"] == "must"
    assert requirements["docker"]["importance"] == "must"


@pytest.mark.parametrize("bad_skill_id", [["python"], {"name": "python"}])
def test_non_string_ai_skill_id_is_dropped_without_crashing(monkeypatch, bad_skill_id) -> None:
    _gemini(monkeypatch, [
        {"skill_id": bad_skill_id, "importance": "must", "quote": "Kubernetes clusters"},
        {"skill_id": "python", "importance": "must", "quote": "strong Python experience"},
    ])
    result = jd_parse.extract_requirements("Backend Engineer Intern", DESCRIPTION)
    assert "python" in _by_skill(result)
    assert any(item["reason"] == "Not a skill in the skill framework." for item in result["dropped"])
