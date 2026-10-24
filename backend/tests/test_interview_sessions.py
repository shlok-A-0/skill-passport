"""Recruiter sign-in, private interview sessions, selection, validation and assessments."""
import copy
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.main as main_module
from app.services import interview_sessions, storage

ALICE = "a" * 40
BOB = "b" * 40
OWNER_TOKEN = "interview-owner-session-secret"
PRIVATE_MARKER = "PRIVATE-ANSWER-POINT"

FLASK_SNIPPET = "\n".join([
    "from flask import Flask",
    "app = Flask(__name__)",
    "",
    "@app.route('/users')",
    "def list_users():",
    "    return query_users(limit=50)",
])
SQL_SNIPPET = "\n".join([
    "def query_users(limit):",
    "    return db.execute('SELECT id, name FROM users LIMIT ?', (limit,))",
])
PY_SNIPPET = "\n".join([
    "def normalise_email(address):",
    "    cleaned = address.strip().lower()",
    "    if '@' not in cleaned:",
    "        raise ValueError('invalid email')",
    "    return cleaned",
])


def _code(repo: str, path: str, start: int, snippet: str, warn: bool = False) -> dict:
    end = start + len(snippet.split("\n")) - 1
    integrity = [{"id": "line_share", "label": "Cited lines written by the candidate",
                  "status": "warn" if warn else "ok", "written": 1 if warn else 5, "cited": 6 if warn else 5,
                  "detail": "x"}]
    return {
        "type": "code", "repo": repo, "file_path": path, "start_line": start, "end_line": end,
        "commit_sha": "abc123", "url": f"https://github.com/demo-user/{repo}/blob/abc123/{path}#L{start}-L{end}",
        "snippet": snippet, "explanation": "Uses it.",
        "checks": {"file_exists": True, "lines_valid": True, "authored": True, "not_fork": True,
                   "signature_found": True},
        "integrity": integrity,
    }


def passport_fixture(passport_id: str = "p-int") -> dict:
    return {
        "id": passport_id,
        "created_at": "2026-10-20T10:00:00+00:00",
        "github_username": "demo-user",
        "target_role": "Backend Developer Intern",
        "sources": {"repos": 2, "resume": True, "certificates": 0},
        "skills": [
            {"skill_id": "flask", "level": "Applied", "certified": False,
             "evidence": [_code("api", "app/routes.py", 10, FLASK_SNIPPET, warn=True)]},
            {"skill_id": "sql", "level": "Applied", "certified": False,
             "evidence": [_code("api", "app/db.py", 3, SQL_SNIPPET)]},
            {"skill_id": "python", "level": "Demonstrated", "certified": False,
             "evidence": [_code("tools", "tools/email.py", 1, PY_SNIPPET)]},
            {"skill_id": "docker", "level": "Claimed", "certified": False, "evidence": []},
        ],
        "rejected_claims": [],
        "role_match": {"role_id": "backend-intern", "role_name": "Backend Developer Intern", "met": 2, "total": 4,
                       "requirements": [
                           {"skill_id": "docker", "importance": "must", "status": "missing", "student_level": None,
                            "min_level": "Used", "reason": "Not found"},
                           {"skill_id": "sql", "importance": "must", "status": "partial", "student_level": "Applied",
                            "min_level": "Demonstrated", "reason": "Requires Demonstrated"},
                           {"skill_id": "flask", "importance": "must", "status": "met", "student_level": "Applied",
                            "min_level": "Applied", "reason": "Met"},
                           {"skill_id": "python", "importance": "nice", "status": "met",
                            "student_level": "Demonstrated", "min_level": "Applied", "reason": "Met"},
                       ]},
        "growth_tasks": [],
        "interview_kit": [{"skill_id": "python", "questions": [
            {"q": "Why does normalise_email lower-case the address?", "good_answer_mentions": "STUDENT-KIT-ANSWER"},
            {"q": "What happens when normalise_email gets no @?", "good_answer_mentions": "STUDENT-KIT-ANSWER"},
        ]}],
        "is_demo": True,
    }


def _question(anchor: str, skill: str, path: str, qtype: str, start: int, end: int, text: str, **extra) -> dict:
    return {"anchor_id": anchor, "skill_id": skill, "file_path": path, "question_type": qtype,
            "focus_start_line": start, "focus_end_line": end, "question": text,
            "follow_up": "Which line would change first, and why?",
            "answer_points": [PRIVATE_MARKER, "Mentions the route"], "task": None, **extra}


def valid_reply() -> str:
    return json.dumps({"questions": [
        _question("T1", "flask", "app/routes.py", "explain", 13, 15,
                  "Walk me through what list_users returns and how the route is registered."),
        _question("T1", "flask", "app/routes.py", "failure_case", 15, 15,
                  "What happens in list_users if query_users raises an exception?"),
        _question("T3", "sql", "app/db.py", "modification", 3, 4,
                  "How would you change query_users to page through users safely?",
                  task={"statement": "Add an offset parameter to query_users without breaking callers.",
                        "expected_behavior": "Returns the next page of users.",
                        "acceptance_criteria": ["Existing calls still work"],
                        "suggested_tests": ["A test that offset=50 skips the first page"],
                        "evaluation_guidance": "Look for parameter binding, not string formatting."}),
        _question("T4", "python", "tools/email.py", "design_tradeoff", 2, 4,
                  "Why does normalise_email raise ValueError instead of returning None?"),
    ]})


def fake_gemini(monkeypatch, replies: list) -> list[str]:
    """Patch the interview generator's Gemini call; returns the prompts it was sent."""
    sent: list[str] = []
    queue = iter(replies)
    monkeypatch.setattr(interview_sessions.config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(interview_sessions.genai, "Client", lambda api_key: object())

    def generate(client, contents):
        sent.append(contents)
        reply = next(queue)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(interview_sessions, "generate_json", generate)
    return sent


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(storage, "DATABASE_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(main_module, "REQUIRE_RECRUITER_AUTH", True)
    monkeypatch.setattr(main_module, "RECRUITER_KEY", "")
    monkeypatch.setattr(main_module, "RECRUITER_ACCOUNTS", {"alice": ALICE, "bob": BOB})
    monkeypatch.setattr(main_module, "ensure_depth_analyzers", lambda passport: passport)  # no GitHub calls
    storage.save_passport(passport_fixture())
    storage.create_stored_job("p-int", target="demo-user",
                              access_token_hash=hashlib.sha256(OWNER_TOKEN.encode()).hexdigest())
    storage.complete_stored_job("p-int", "p-int", [])
    return TestClient(main_module.app)


def _login(client: TestClient, key: str) -> dict:
    token = client.post("/auth/recruiter/login", json={"key": key}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _create(client: TestClient, headers: dict, key: str = "click-1"):
    return client.post("/passport/p-int/interview-sessions", headers={**headers, "Idempotency-Key": key})


# --- Sign-in ---------------------------------------------------------------------------------------

def test_login_issues_a_session_token_and_logout_ends_it(client) -> None:
    assert client.post("/auth/recruiter/login", json={"key": "wrong"}).status_code == 401
    login = client.post("/auth/recruiter/login", json={"key": ALICE})
    assert login.status_code == 200 and login.json()["recruiter_id"] == "alice"
    token = login.json()["token"]
    assert ALICE not in login.text and token != ALICE
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).json() == {
        "role": "recruiter", "recruiter_id": "alice", "auth_required": True}
    assert client.get("/passport/p-int/review", headers=headers).status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/auth/me", headers=headers).json()["role"] is None
    assert client.get("/passport/p-int/review", headers=headers).status_code == 401


ANONYMOUS = {"role": None, "recruiter_id": None, "auth_required": True}


def test_removing_a_recruiter_key_ends_their_sessions(client, monkeypatch) -> None:
    headers = _login(client, ALICE)
    monkeypatch.setattr(main_module, "RECRUITER_ACCOUNTS", {"bob": BOB})
    assert client.get("/auth/me", headers=headers).json() == ANONYMOUS
    assert client.get("/passport/p-int/review", headers=headers).status_code == 401


def test_students_and_anonymous_users_cannot_sign_in_as_recruiters(client) -> None:
    assert client.get("/auth/me").json() == ANONYMOUS
    assert client.get("/auth/me", headers={"X-Passport-Token": OWNER_TOKEN}).json() == ANONYMOUS
    # A role or recruiter id in the request never grants access.
    assert client.get("/auth/me?role=recruiter&recruiter_id=alice").json() == ANONYMOUS
    assert client.get("/auth/me", headers={"Authorization": "Bearer not-a-session"}).json() == ANONYMOUS
    assert client.get("/passport/p-int/review", headers={"X-Passport-Token": OWNER_TOKEN}).status_code == 401


# --- Authorization ----------------------------------------------------------------------------------

def test_interview_routes_reject_unauthenticated_and_student_requests(client, monkeypatch) -> None:
    fake_gemini(monkeypatch, [valid_reply()])
    alice = _login(client, ALICE)
    session = _create(client, alice).json()
    question_id = session["questions"][0]["id"]
    student = {"X-Passport-Token": OWNER_TOKEN}
    for headers in ({}, student):
        assert client.post("/passport/p-int/interview-sessions", headers=headers).status_code == 401
        assert client.get("/passport/p-int/interview-sessions", headers=headers).status_code == 401
        # Knowing the session id is not enough.
        assert client.get(f"/interview-sessions/{session['id']}", headers=headers).status_code == 401
        assert client.get(f"/interview-sessions/{session['id']}/summary", headers=headers).status_code == 401
        put = client.put(f"/interview-sessions/{session['id']}/questions/{question_id}/assessment",
                         headers=headers, json={"outcome": "explained_well"})
        assert put.status_code == 401
    # Nothing was recorded by those attempts.
    assert client.get(f"/interview-sessions/{session['id']}/summary", headers=alice).json()["assessed"] == 0


def test_a_recruiter_cannot_read_or_assess_another_recruiters_session(client, monkeypatch) -> None:
    fake_gemini(monkeypatch, [valid_reply()])
    alice, bob = _login(client, ALICE), _login(client, BOB)
    session = _create(client, alice).json()
    question_id = session["questions"][0]["id"]
    assert client.get(f"/interview-sessions/{session['id']}", headers=bob).status_code == 404
    assert client.put(f"/interview-sessions/{session['id']}/questions/{question_id}/assessment",
                      headers=bob, json={"outcome": "could_not_explain"}).status_code == 404
    assert client.get("/passport/p-int/interview-sessions", headers=bob).json()["items"] == []
    assert len(client.get("/passport/p-int/interview-sessions", headers=alice).json()["items"]) == 1


def test_student_responses_never_include_recruiter_sessions(client, monkeypatch) -> None:
    fake_gemini(monkeypatch, [valid_reply()])
    alice = _login(client, ALICE)
    session = _create(client, alice).json()
    client.put(f"/interview-sessions/{session['id']}/questions/{session['questions'][0]['id']}/assessment",
               headers=alice, json={"outcome": "could_not_explain", "note": "PRIVATE-NOTE"})
    student = client.get("/passport/p-int")
    assert student.status_code == 200
    for private in (PRIVATE_MARKER, "PRIVATE-NOTE", session["id"], "answer_points", "could_not_explain",
                    "list_users returns", "STUDENT-KIT-ANSWER"):
        assert private not in student.text
    # The student practice kit stays available, without its answer notes.
    kit = student.json()["interview_kit"]
    assert [item["skill_id"] for item in kit] == ["python"]
    assert kit[0]["questions"][0] == {"q": "Why does normalise_email lower-case the address?"}
    # The recruiter passport review is unchanged by sessions too.
    review = client.get("/passport/p-int/review", headers=alice)
    assert PRIVATE_MARKER not in review.text and "PRIVATE-NOTE" not in review.text


# --- Selection --------------------------------------------------------------------------------------

def test_selection_puts_job_critical_attribution_concerns_first() -> None:
    targets = interview_sessions.select_interview_targets(passport_fixture())
    assert [(t["skill_id"], t["kind"], t["tier"]) for t in targets] == [
        ("flask", "code", 1),       # must-have, cited lines mostly not the candidate's
        ("docker", "discussion", 2),  # must-have, no code citation: discussion, not an invented citation
        ("sql", "code", 2),         # must-have below its minimum level
        ("python", "code", 4),      # strong evidence for deeper questions
    ]
    flask, docker = targets[0], targets[1]
    assert any(reason.startswith("Attribution needs review") for reason in flask["reasons"])
    assert "Job-critical requirement" in flask["reasons"]
    assert docker["citation"] is None and "not evidence of a missing skill" in docker["reasons"][-1]
    assert targets[3]["reasons"][-1] == interview_sessions.REASON_STRONG


def test_selection_is_deterministic_and_never_changes_the_passport() -> None:
    passport = passport_fixture()
    before = copy.deepcopy(passport)
    first = interview_sessions.select_interview_targets(passport)
    shuffled = copy.deepcopy(passport)
    shuffled["skills"].reverse()
    assert interview_sessions.select_interview_targets(shuffled) == first
    assert interview_sessions.select_interview_targets(passport) == first
    assert passport == before


def test_strong_skills_are_used_when_nothing_higher_priority_exists() -> None:
    passport = passport_fixture()
    passport["role_match"]["requirements"] = []
    passport["skills"][0]["evidence"][0]["integrity"][0]["status"] = "ok"
    targets = interview_sessions.select_interview_targets(passport)
    assert [t["skill_id"] for t in targets] == ["flask", "python", "sql"]
    assert all(t["tier"] == 4 and t["kind"] == "code" for t in targets)


def test_selection_skips_citations_whose_lines_do_not_match_their_range() -> None:
    passport = passport_fixture()
    passport["skills"][1]["evidence"][0]["end_line"] = 99  # stored snippet no longer matches the range
    passport["skills"][1]["evidence"][0]["record"] = {"snippet_sha256": "0" * 64}
    targets = {t["skill_id"]: t for t in interview_sessions.select_interview_targets(passport)}
    # SQL has no usable citation left, so it becomes a discussion instead of citing bad lines.
    assert targets["sql"]["kind"] == "discussion" and targets["sql"]["citation"] is None


def test_selection_avoids_reusing_a_file() -> None:
    passport = passport_fixture()
    passport["skills"][1]["evidence"].insert(0, _code("api", "app/routes.py", 13, "def list_users():\n    return query_users(limit=50)"))
    targets = {t["skill_id"]: t for t in interview_sessions.select_interview_targets(passport)}
    assert targets["flask"]["citation"]["file_path"] == "app/routes.py"
    assert targets["sql"]["citation"]["file_path"] == "app/db.py"


def test_a_skill_cited_only_in_an_already_chosen_file_is_skipped() -> None:
    passport = passport_fixture()
    # SQL's only citation is now in the file already chosen for Flask.
    passport["skills"][1]["evidence"] = [_code("api", "app/routes.py", 13, "def list_users():\n    return query_users(limit=50)")]
    targets = interview_sessions.select_interview_targets(passport)
    files = [(t["citation"]["repo"], t["citation"]["file_path"]) for t in targets if t["citation"]]
    assert len(files) == len(set(files))
    assert "sql" not in [t["skill_id"] for t in targets]
    assert [t["skill_id"] for t in targets] == ["flask", "docker", "python"]


# --- Generation and validation ----------------------------------------------------------------------

def _targets() -> list[dict]:
    return [t for t in interview_sessions.select_interview_targets(passport_fixture()) if t["kind"] == "code"]


def test_valid_questions_are_accepted(monkeypatch) -> None:
    sent = fake_gemini(monkeypatch, [valid_reply()])
    questions, dropped = interview_sessions.generate_questions(_targets(), "Backend", [])
    assert [q.anchor_id for q in questions] == ["T1", "T1", "T3", "T4"]
    assert dropped == [] and len(sent) == 1
    assert "13| @app.route('/users')" in sent[0]  # numbered source lines are the grounding


@pytest.mark.parametrize("change, reason", [
    ({"skill_id": "kubernetes"}, "does not match the selected evidence"),
    ({"anchor_id": "T9"}, "not selected"),
    ({"file_path": "app/invented.py"}, "not the selected evidence"),
    ({"focus_start_line": 14, "focus_end_line": 40}, "outside the cited lines"),
    ({"question": "What is the difference between a list and a tuple in general?"}, "does not refer to the cited code"),
    # A file name, a line number or a follow-up that names the code is not enough on its own.
    ({"question": "What does routes.py do on line 15, in general terms?",
      "follow_up": "Why does list_users call query_users here?"}, "does not refer to the cited code"),
])
def test_invalid_questions_are_rejected(monkeypatch, change, reason) -> None:
    bad = {**json.loads(valid_reply())["questions"][1], **change}
    good = json.loads(valid_reply())["questions"]
    fake_gemini(monkeypatch, [json.dumps({"questions": [bad, good[0], good[2], good[3]]})])
    questions, dropped = interview_sessions.generate_questions(_targets(), "Backend", [])
    assert len(questions) == 3
    assert len(dropped) == 1 and reason in dropped[0]["reason"].lower()


def _grounded(question: str, snippet: str, start: int = 1) -> bool:
    lines = snippet.split("\n")
    citation = {"snippet": snippet, "snippet_start_line": start, "start_line": start,
                "end_line": start + len(lines) - 1, "file_path": "calc/math_utils.py"}
    proposed = interview_sessions.ProposedQuestion(
        anchor_id="T1", skill_id="python", file_path="calc/math_utils.py", question_type="explain",
        focus_start_line=start, focus_end_line=start + len(lines) - 1, question=question,
        follow_up="Which line would you change first?", answer_points=["x"],
    )
    return interview_sessions._refers_to_code(proposed, citation)


def test_short_names_in_the_cited_code_can_ground_a_question() -> None:
    short = "def add(a, b):\n    return a + b"
    assert _grounded("What does add return when one argument is None?", short)
    assert _grounded("Why does `add` take two arguments?", short)
    # Single letters, file names and line numbers never count.
    assert not _grounded("What is a in this function on line 2 of math_utils.py?", short)
    # With longer names present, a short name must be written as code to count.
    mixed = "def add(total, amount):\n    return total + amount"
    assert _grounded("What happens in add( ) when amount is negative?", mixed)
    assert _grounded("Why is add(...) a pure function?", mixed)
    assert not _grounded("How would you add logging here?", mixed)


def test_sql_query_identifiers_ground_questions() -> None:
    sql_example = (
        'rows = conn.execute(\n'
        '    text("SELECT category, SUM(amount_cents) AS total FROM expenses "\n'
        '         "WHERE spent_on >= :start AND spent_on < :end "\n'
        '         "GROUP BY category ORDER BY total DESC"),\n'
        '    {"start": start, "end": end},\n'
        ')'
    )
    assert _grounded("Why are expenses grouped by category in this query?", sql_example)
    assert _grounded("Why did you aggregate amount_cents?", sql_example)
    assert _grounded("How does the query filter on spent_on?", sql_example)
    # Generic SQL keywords alone do not ground questions.
    assert not _grounded("Why did you use select here?", sql_example)
    assert not _grounded("Can you explain this where clause?", sql_example)

    # db.execute
    db_exec = "return db.execute('SELECT id, name FROM users LIMIT ?', (limit,))"
    assert _grounded("What users are returned by the query?", db_exec)
    assert _grounded("Why does it query name from users?", db_exec)

    # Quoted SQL names
    quoted = 'SELECT "amount_cents", "category" FROM "expenses"'
    assert _grounded("What is amount_cents in expenses?", quoted)
    # Python and JavaScript identifiers (including count, update) remain valid
    assert _grounded("Why is count incremented here?", "count += 1")
    assert _grounded("What does update do?", "def update(item):\n    return item")


def test_comment_and_string_words_do_not_ground_a_question() -> None:
    # Ordinary words in a comment or a string literal name no identifier, however suggestive.
    assert not _grounded("When should an application cache results?", "x = 1  # Cache results here")
    assert not _grounded("When should an application cache results?", 'message = "Cache results here"')
    assert not _grounded("When should an application cache results?", 'message = "cache"')
    assert not _grounded("When should an application cache results?", "const ttl = 60; // cache results")
    assert not _grounded("When should an application cache results?", "/* cache results */\nconst ttl = 60;")
    assert not _grounded("When should an application cache results?", "SELECT 1; -- cache results")
    # The same idea as an actual identifier still grounds the question.
    assert _grounded("When should cache_results be called?", "def cache_results(rows):\n    return rows")
    # And a comment does not strip the real code sharing its line.
    assert _grounded("When should cache_results be called?", "def cache_results(rows):  # Cache results here\n    return rows")


def test_malformed_output_is_retried_then_fails_safely(monkeypatch) -> None:
    sent = fake_gemini(monkeypatch, ["not json", valid_reply()])
    questions, _ = interview_sessions.generate_questions(_targets(), "Backend", [])
    assert len(questions) == 4 and len(sent) == 2
    fake_gemini(monkeypatch, ["not json", '{"items": []}'])
    with pytest.raises(interview_sessions.InterviewGenerationError):
        interview_sessions.generate_questions(_targets(), "Backend", [])


def test_model_errors_become_a_generation_error(monkeypatch) -> None:
    fake_gemini(monkeypatch, [TimeoutError("slow")])
    with pytest.raises(interview_sessions.InterviewGenerationError, match="unavailable"):
        interview_sessions.generate_questions(_targets(), "Backend", [])


def test_only_one_code_task_per_set_and_only_on_modification(monkeypatch) -> None:
    task = json.loads(valid_reply())["questions"][2]["task"]
    reply = json.loads(valid_reply())
    reply["questions"][0]["task"] = task  # an explain question cannot carry a task
    reply["questions"][3] = {**reply["questions"][3], "question_type": "modification", "task": task}
    fake_gemini(monkeypatch, [json.dumps(reply)] * 2)
    questions, dropped = interview_sessions.generate_questions(_targets(), "Backend", [])
    assert sum(1 for q in questions if q.task) == 1
    assert any("modification" in item["reason"] for item in dropped)


def test_recruiter_generation_uses_no_disk_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main_module.config, "CACHE_DIR", tmp_path)
    fake_gemini(monkeypatch, [valid_reply(), valid_reply()])
    interview_sessions.generate_questions(_targets(), "Backend", [])
    assert list(tmp_path.iterdir()) == []  # nothing a student-kit lookup could ever return


# --- Sessions through the API -------------------------------------------------------------------------

def test_session_is_saved_and_reopening_does_not_regenerate(client, monkeypatch) -> None:
    sent = fake_gemini(monkeypatch, [valid_reply()])
    alice = _login(client, ALICE)
    created = _create(client, alice)
    assert created.status_code == 201
    session = created.json()
    assert session["status"] == "ready"
    # 4 generated questions plus the Docker discussion (no code citation).
    assert [q["question_type"] for q in session["questions"]] == [
        "explain", "failure_case", "modification", "design_tradeoff", "discussion"]
    first = session["questions"][0]
    assert first["anchor"]["url"].endswith("app/routes.py#L10-L15")
    assert first["anchor"]["focus_url"].endswith("#L13-L15")
    assert first["anchor"]["commit_sha"] == "abc123" and first["anchor"]["snippet_sha256"]
    assert first["requirement"]["importance"] == "must" and first["selection_reasons"]
    # Warnings use the evidence packet's wording, not the check's name.
    assert first["attribution_warnings"] == ["few cited lines written by the candidate"]
    assert session["questions"][2]["task"]["statement"]
    assert session["questions"][-1]["anchor"] is None
    # Reopening and retrying the same click return the stored set with no new model call.
    assert client.get(f"/interview-sessions/{session['id']}", headers=alice).json()["questions"] == session["questions"]
    retry = _create(client, alice, key="click-1")
    assert retry.status_code == 200 and retry.json()["id"] == session["id"]
    assert len(sent) == 1


def test_a_new_session_is_a_separate_set_and_avoids_earlier_questions(client, monkeypatch) -> None:
    sent = fake_gemini(monkeypatch, [valid_reply(), valid_reply()])
    alice = _login(client, ALICE)
    first = _create(client, alice, key="click-1").json()
    second = _create(client, alice, key="click-2").json()
    assert first["id"] != second["id"]
    assert "Avoid repeating these earlier questions" in sent[1]
    assert len(client.get("/passport/p-int/interview-sessions", headers=alice).json()["items"]) == 2


def test_failed_generation_is_not_saved_as_a_valid_session(client, monkeypatch) -> None:
    fake_gemini(monkeypatch, [RuntimeError("503"), valid_reply()])
    alice = _login(client, ALICE)
    failed = _create(client, alice, key="click-1")
    assert failed.status_code == 503 and "unavailable" in failed.json()["detail"]
    sessions = client.get("/passport/p-int/interview-sessions", headers=alice).json()["items"]
    assert [(item["status"], len(item["questions"])) for item in sessions] == [("failed", 0)]
    # Same key: still the failed attempt. A new key retries.
    assert _create(client, alice, key="click-1").status_code == 503
    assert _create(client, alice, key="click-2").status_code == 201


def test_a_set_still_generating_blocks_a_double_submit(client) -> None:
    alice = _login(client, ALICE)
    storage.start_interview_session("p-int", "alice", "in-flight", "rev")
    assert _create(client, alice, key="click-2").status_code == 409


def _backdate(session_id: str, *, created_at: str | None = None, updated_at: str | None = None) -> None:
    with storage._connect() as connection:
        if created_at is not None:
            connection.execute("UPDATE interview_sessions SET created_at = ? WHERE id = ?", (created_at, session_id))
        if updated_at is not None:
            connection.execute("UPDATE interview_sessions SET updated_at = ? WHERE id = ?", (updated_at, session_id))


def test_a_slow_live_generation_keeps_its_reservation(client) -> None:
    # Past five minutes but still heartbeating: not reported interrupted, not replaceable.
    alice = _login(client, ALICE)
    session, outcome = storage.start_interview_session("p-int", "alice", "slow-1", "rev")
    assert outcome == "created"
    started = (datetime.now(timezone.utc) - timedelta(
        seconds=storage.INTERVIEW_GENERATION_TIMEOUT_SECONDS + 100)).isoformat()
    _backdate(session["id"], created_at=started)
    storage.touch_interview_session(session["id"])
    assert _create(client, alice, key="click-2").status_code == 409
    current = client.get(f"/interview-sessions/{session['id']}", headers=alice).json()
    assert current["status"] == "generating" and current["error"] is None


def test_an_interrupted_generation_is_ended_before_a_replacement(client, monkeypatch) -> None:
    fake_gemini(monkeypatch, [valid_reply()])
    alice = _login(client, ALICE)
    stale, _ = storage.start_interview_session("p-int", "alice", "stale-1", "rev")
    old = (datetime.now(timezone.utc) - timedelta(
        seconds=storage.INTERVIEW_GENERATION_TIMEOUT_SECONDS + 100)).isoformat()
    _backdate(stale["id"], created_at=old, updated_at=old)
    # No heartbeat past the timeout: reported as interrupted...
    interrupted = client.get(f"/interview-sessions/{stale['id']}", headers=alice).json()
    assert interrupted["status"] == "failed" and "interrupted" in interrupted["error"].lower()
    # ...a replacement is allowed to start...
    assert _create(client, alice, key="click-2").status_code == 201
    # ...and the original work can no longer save over the new set.
    with pytest.raises(RuntimeError, match="Cannot complete"):
        storage.complete_interview_session(stale["id"], {}, [])
    ended = client.get(f"/interview-sessions/{stale['id']}", headers=alice).json()
    assert ended["status"] == "failed" and "interrupted" in ended["error"].lower()


def test_questions_are_flagged_stale_after_the_passport_changes(client, monkeypatch) -> None:
    fake_gemini(monkeypatch, [valid_reply()])
    alice = _login(client, ALICE)
    session = _create(client, alice).json()
    assert not any(q["stale"] for q in session["questions"])
    rechecked = passport_fixture()
    rechecked["updated_at"] = datetime.now(timezone.utc).isoformat()
    rechecked["skills"][0]["evidence"][0]["commit_sha"] = "def456"  # Flask citation moved on
    storage.save_passport(rechecked)
    reopened = client.get(f"/interview-sessions/{session['id']}", headers=alice).json()
    assert reopened["passport_changed"] is True
    assert [q["stale"] for q in reopened["questions"]] == [True, True, False, False, False]
    assert "re-checked" in reopened["questions"][0]["stale_reason"]


# --- Assessments ---------------------------------------------------------------------------------------

def test_outcomes_notes_and_summary_persist_without_touching_evidence(client, monkeypatch) -> None:
    fake_gemini(monkeypatch, [valid_reply()])
    alice = _login(client, ALICE)
    session = _create(client, alice).json()
    ids = [q["id"] for q in session["questions"]]
    before = storage.get_passport("p-int")
    review_before = client.get("/passport/p-int/review", headers=alice).json()
    path = f"/interview-sessions/{session['id']}/questions/{{}}/assessment"

    summary = client.get(f"/interview-sessions/{session['id']}/summary", headers=alice).json()
    assert summary["text"] == "Interview check: 0 of 5 questions explained well · 5 not yet assessed"

    for question_id, outcome in zip(ids, ("explained_well", "partly_explained", "could_not_explain")):
        saved = client.put(path.format(question_id), headers=alice, json={"outcome": outcome, "note": f"note {outcome}"})
        assert saved.status_code == 200
        assert saved.json()["assessment"]["assessor_id"] == "alice"
        assert saved.json()["assessment"]["assessed_at"]
    summary = client.get(f"/interview-sessions/{session['id']}/summary", headers=alice).json()
    assert (summary["explained_well"], summary["partly_explained"], summary["could_not_explain"]) == (1, 1, 1)
    assert summary["assessed"] == 3 and summary["unassessed"] == 2
    assert summary["text"] == "Interview check: 1 of 5 questions explained well · 2 not yet assessed"

    # Changing an outcome replaces it; the summary follows after a reload.
    client.put(path.format(ids[2]), headers=alice, json={"outcome": "explained_well", "note": ""})
    reopened = client.get(f"/interview-sessions/{session['id']}", headers=alice).json()
    assert reopened["summary"]["explained_well"] == 2 and reopened["summary"]["could_not_explain"] == 0
    assert reopened["questions"][0]["assessment"]["note"] == "note explained_well"

    # Evidence, levels and role match are untouched.
    assert storage.get_passport("p-int") == before
    review_after = client.get("/passport/p-int/review", headers=alice).json()
    for field in ("skills", "role_match", "requirement_packets", "rejected_claims"):
        assert review_after[field] == review_before[field]


def test_assessment_input_is_validated(client, monkeypatch) -> None:
    fake_gemini(monkeypatch, [valid_reply()])
    alice = _login(client, ALICE)
    session = _create(client, alice).json()
    path = f"/interview-sessions/{session['id']}/questions/{session['questions'][0]['id']}/assessment"
    assert client.put(path, headers=alice, json={"outcome": "proved_copying"}).status_code == 422
    assert client.put(path, headers=alice, json={"outcome": "explained_well", "note": "x" * 501}).status_code == 422
    other = f"/interview-sessions/{session['id']}/questions/not-in-this-session/assessment"
    assert client.put(other, headers=alice, json={"outcome": "explained_well"}).status_code == 404


def test_summary_counts_only_saved_outcomes() -> None:
    questions = [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}]
    summary = interview_sessions.session_summary(questions, {"q1": {"outcome": "explained_well"}})
    assert summary["explained_well"] == 1 and summary["unassessed"] == 2
    assert interview_sessions.session_summary(questions, {})["explained_well"] == 0
    one = interview_sessions.session_summary([{"id": "q1"}], {"q1": {"outcome": "explained_well"}})
    assert one["text"] == "Interview check: 1 of 1 question explained well"
