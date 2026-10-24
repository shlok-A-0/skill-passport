import copy
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config
from app import main as main_module
from app.services import pipeline
from app.services.sql_depth import (
    NOTE,
    WEIGHTS,
    _label,
    compute_sql_depth,
    safe_sql_depth,
    student_sql_depth,
)


def source(text: str, author: str = "student", authored: bool = True, blame: bool = True) -> dict:
    text = textwrap.dedent(text).strip("\n") + "\n"
    lines = text.splitlines()
    return {
        "content": text,
        "lines": lines,
        "authored_shas": ["sha1"] if authored else [],
        "line_count": len(lines),
        "blame": [[1, len(lines), author]] if blame else None,
    }


def repo(name: str, files: dict[str, dict], tests: dict[str, str] | None = None,
         test_authored: bool = True, ci_passed: bool = False) -> dict:
    tests = {path: textwrap.dedent(content) for path, content in (tests or {}).items()}
    return {
        "name": name,
        "full_name": f"student/{name}",
        "head_sha": "abc123",
        "is_fork": False,
        "tree": [*files, *tests],
        "source_files": files,
        "test_files": list(tests),
        "test_contents": tests,
        "test_authored": {path: test_authored for path in tests},
        "ci_status": {"has_successful_run": True} if ci_passed else None,
    }


def ingestion(*repos: dict) -> dict:
    return {"username": "student", "repos": list(repos)}


def depth(*repos: dict, rejected: list | None = None, skills: list | None = None) -> dict:
    return compute_sql_depth(ingestion(*repos), {"skills": skills or [], "rejected_claims": rejected or []})


def values(result: dict) -> dict[str, float]:
    return {key: part["value"] for key, part in result["parts"].items()}


# --- Fixtures ----------------------------------------------------------------------------------

BASIC_SCRIPT = """
import sqlite3

conn = sqlite3.connect("app.db")
for row in conn.execute("SELECT * FROM users"):
    print(row)
"""

SCHEMA_SQL = """
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE
);

CREATE TABLE projects (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL
);

CREATE TABLE tasks (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL
);
"""

TASKS_PY = '''
import sqlite3

from flask import Flask, request

app = Flask(__name__)


def connect():
    return sqlite3.connect("tasks.db")


def init_db(conn):
    with open("app/schema.sql") as handle:
        conn.executescript(handle.read())


@app.post("/projects")
def create_project():
    conn = connect()
    conn.execute("INSERT INTO projects (owner_id, name) VALUES (?, ?)", (request.json["owner_id"], request.json["name"]))
    conn.commit()
    return {"ok": True}, 201


@app.post("/tasks")
def create_task():
    conn = connect()
    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO tasks (project_id, title, status, created_at) VALUES (?, ?, 'open', ?)",
        (request.json["project_id"], request.json["title"], request.json["created_at"]),
    )
    conn.execute("COMMIT")
    return {"ok": True}, 201


@app.patch("/tasks/<int:task_id>")
def update_status(task_id):
    conn = connect()
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (request.json["status"], task_id))
    conn.commit()
    return {"ok": True}


@app.delete("/tasks/<int:task_id>")
def delete_task(task_id):
    connect().execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return {"ok": True}


def list_tasks(conn, owner_id):
    return conn.execute(
        """
        SELECT t.id, t.title, p.name
        FROM tasks t
        JOIN projects p ON p.id = t.project_id
        WHERE p.owner_id = ?
        ORDER BY t.created_at DESC
        """,
        (owner_id,),
    ).fetchall()


@app.get("/users/<int:owner_id>/tasks")
def tasks_for_user(owner_id):
    return {"tasks": list_tasks(connect(), owner_id)}
'''

TEST_TASKS = """
from app.tasks import create_task, list_tasks


def test_list_tasks_returns_the_owners_tasks(conn):
    rows = list_tasks(conn, 1)
    assert rows == [(1, "Write docs", "Website")]
"""

REPORTS_PY = '''
import pandas as pd
from sqlalchemy import text

OVERDUE = """
SELECT t.id, t.title
FROM tasks t
WHERE t.status = 'open'
  AND t.project_id IN (SELECT id FROM projects WHERE owner_id = :owner)
"""


def weekly_report(engine, owner_id):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            WITH recent AS (
                SELECT p.name AS project, t.status, t.created_at
                FROM tasks t
                JOIN projects p ON p.id = t.project_id
                WHERE t.created_at >= :since
            )
            SELECT project, status, COUNT(*) AS total,
                   RANK() OVER (PARTITION BY project ORDER BY COUNT(*) DESC) AS status_rank
            FROM recent
            GROUP BY project, status
        """), {"since": "2026-01-01"}).fetchall()
    return rows


def overdue(engine, owner_id):
    return pd.read_sql(OVERDUE, engine, params={"owner": owner_id})


def status_counts(engine):
    return pd.read_sql("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status", engine)
'''

API_PY = """
from fastapi import FastAPI

from insights.reports import overdue, status_counts, weekly_report

api = FastAPI()
engine = None


@api.get("/reports/weekly/{owner_id}")
def weekly(owner_id: int):
    return weekly_report(engine, owner_id)


@api.get("/reports/overdue/{owner_id}")
def overdue_tasks(owner_id: int):
    return overdue(engine, owner_id)


@api.get("/reports/status")
def statuses():
    return status_counts(engine)
"""

TEST_REPORTS = """
from insights.reports import status_counts


def test_status_counts_groups_by_status(engine):
    frame = status_counts(engine)
    assert list(frame["status"]) == ["done", "open"]
"""


def basic_repos() -> list[dict]:
    return [repo("scripts", {"dump_users.py": source(BASIC_SCRIPT)})]


def rich_repos() -> list[dict]:
    return [
        repo(
            "task-tracker",
            {"app/schema.sql": source(SCHEMA_SQL), "app/tasks.py": source(TASKS_PY)},
            tests={"tests/test_tasks.py": TEST_TASKS},
        ),
        repo(
            "task-insights",
            {"insights/reports.py": source(REPORTS_PY), "insights/api.py": source(API_PY)},
            tests={"tests/test_reports.py": TEST_REPORTS},
            ci_passed=True,
        ),
    ]


# --- Fixture scores ----------------------------------------------------------------------------


def test_basic_select_star_scores_at_most_25() -> None:
    result = depth(*basic_repos())
    assert result["distinct_query_count"] == 1
    assert result["score"] <= 25
    assert result["label"] == "Limited evidence"
    assert result["note"] == NOTE


def test_rich_fixture_scores_at_least_70_without_the_one_query_cap() -> None:
    result = depth(*rich_repos())
    assert result["distinct_query_count"] >= 3
    assert result["score"] >= 70
    assert result["cap_reason"] is None
    assert result["label"] in {"Solid", "Strong"}
    # The score is the weighted sum of the parts, each part showing its points and its maximum.
    raw = sum(part["value"] * WEIGHTS[key] for key, part in result["parts"].items())
    assert result["score"] == int(raw + 0.5)
    for key, part in result["parts"].items():
        assert part["max_points"] == round(WEIGHTS[key] * 100)
        assert 0 <= part["points"] <= part["max_points"]
        assert part["reason"]
    assert not result["concepts_missing"]


# --- Extraction --------------------------------------------------------------------------------


def test_extracts_execute_text_read_sql_and_sql_file_statements_with_lines() -> None:
    result = depth(*rich_repos())
    shapes = [query["shape"] for query in result["distinct_queries"]]
    assert any(shape.startswith("INSERT INTO projects") for shape in shapes)  # execute(...)
    assert any(shape.startswith("WITH recent AS") for shape in shapes)  # execute(text(...)), read once
    assert any("GROUP BY status" in shape for shape in shapes)  # read_sql(...)
    assert any("IN (SELECT id FROM projects" in shape for shape in shapes)  # read_sql(VARIABLE)
    schema = next(item for item in result["concepts_found"] if item["id"] == "schema_design")
    first = schema["evidence"][0]
    assert first["file_path"] == "app/schema.sql" and (first["start_line"], first["end_line"]) == (1, 4)
    assert first["url"] == "https://github.com/student/task-tracker/blob/abc123/app/schema.sql#L1-L4"
    assert first["snippet"].startswith("CREATE TABLE users")
    joined = next(q for q in result["distinct_queries"] if q["shape"].startswith("SELECT t.id, t.title, p.name"))
    # The statement's own lines inside the triple-quoted string, not the whole call.
    snippet = joined["evidence"][0]["snippet"].splitlines()
    assert snippet[0].strip() == "SELECT t.id, t.title, p.name"
    assert snippet[-1].strip() == "ORDER BY t.created_at DESC"


def test_backslash_suppressed_literal_newline_uses_safe_source_range() -> None:
    code = "def run(conn):\n    conn.execute(\"\"\"\\\nSELECT id\nFROM users\nWHERE id = 1\n\"\"\")\n"
    result = depth(repo("escaped", {"query.py": source(code)}))
    query = result["distinct_queries"][0]["evidence"][0]
    assert (query["start_line"], query["end_line"]) == (2, 6)
    assert "SELECT id" in query["snippet"] and "WHERE id = 1" in query["snippet"]


def test_text_from_other_libraries_and_non_sql_strings_are_ignored() -> None:
    code = """
    import streamlit as st

    def page(conn):
        st.text("Select an option from the list")
        conn.execute("Hello there")
    """
    result = depth(repo("ui", {"page.py": source(code)}))
    assert result["distinct_query_count"] == 0 and result["skipped_total"] == 0


def test_runtime_built_sql_is_reported_not_guessed() -> None:
    code = """
    def find(conn, table, name):
        return conn.execute(f"SELECT * FROM {table} WHERE name = ?", (name,))

    def run(conn, sql):
        return conn.execute(sql)
    """
    result = depth(repo("dyn", {"dyn.py": source(code)}))
    assert result["distinct_query_count"] == 0
    assert [item["kind"] for item in result["skipped"]] == ["dynamic"]
    assert any("couldn't be resolved" in note for note in result["notes"])


def test_unparseable_sql_is_skipped_and_not_counted() -> None:
    sql = """
    SELECT FROM WHERE;
    SELECT id FROM users WHERE id = 1;
    """
    result = depth(repo("broken", {"queries.sql": source(sql)}))
    assert result["distinct_query_count"] == 1
    skipped = [item for item in result["skipped"] if item["kind"] == "unparseable"]
    assert len(skipped) == 1 and skipped[0]["start_line"] == 1
    assert result["parts"]["complexity"]["value"] == 25


def test_sql_in_test_files_is_not_scored() -> None:
    result = depth(repo("t", {"tests/test_db.py": source('def test_x(c):\n    c.execute("SELECT * FROM users")\n')}))
    assert result["distinct_query_count"] == 0


# --- Deduplication -----------------------------------------------------------------------------


def test_formatting_literals_case_and_column_changes_are_one_query() -> None:
    code = '''
    def a(conn):
        conn.execute("SELECT name FROM users WHERE id = 1")

    def b(conn):
        conn.execute("""select   EMAIL
                        from USERS
                        where ID = ?""")

    def c(conn):
        conn.execute("SELECT name FROM users WHERE id = 1")

    def d(conn):
        conn.execute("SELECT name FROM users WHERE id IN (1, 2, 3)")

    def e(conn):
        conn.execute("SELECT email FROM users WHERE id IN (4, 5)")
    '''
    result = depth(repo("dupes", {"dupes.py": source(code)}))
    # "= ?" and "IN (...)" are different predicates; IN-list length is not.
    assert result["distinct_query_count"] == 2
    assert [q["occurrence_count"] for q in result["distinct_queries"]] == [3, 2]
    assert result["parts"]["repetition"]["value"] == 50
    assert "3 repeated or trivially different use(s) were merged" in result["parts"]["repetition"]["reason"]


def test_the_same_query_in_two_repositories_is_counted_once() -> None:
    code = 'def f(conn):\n    conn.execute("SELECT id FROM tasks WHERE status = ?")\n'
    result = depth(repo("one", {"one.py": source(code)}), repo("two", {"two.py": source(code)}))
    assert result["distinct_query_count"] == 1


def test_different_tables_and_structures_are_distinct() -> None:
    code = """
    def f(conn):
        conn.execute("SELECT id FROM tasks")
        conn.execute("SELECT id FROM users")
        conn.execute("SELECT id FROM users WHERE id = ?")
    """
    assert depth(repo("r", {"r.py": source(code)}))["distinct_query_count"] == 3


# --- Authorship and exclusions -----------------------------------------------------------------


QUERIES = """
def f(conn):
    conn.execute("SELECT id FROM tasks WHERE status = ?")
    conn.execute("SELECT id FROM users ORDER BY id")
    conn.execute("SELECT COUNT(*) FROM tasks GROUP BY status")
"""


def test_files_the_candidate_never_committed_to_are_not_counted() -> None:
    result = depth(repo("r", {"q.py": source(QUERIES, authored=False)}))
    assert result["distinct_query_count"] == 0
    assert {item["kind"] for item in result["skipped"]} == {"not_committed"}


def test_queries_with_no_candidate_written_lines_are_excluded() -> None:
    result = depth(repo("r", {"q.py": source(QUERIES, author="someone-else")}))
    assert result["distinct_query_count"] == 0
    assert {item["kind"] for item in result["skipped"]} == {"not_written"}


def test_authorship_is_the_mean_line_share_per_distinct_query() -> None:
    files = source(QUERIES)
    files["blame"] = [[1, 2, "student"], [3, 3, "someone-else"], [4, 4, "student"]]
    result = depth(repo("r", {"q.py": files}))
    # Line 3's query has no candidate lines and is excluded; the other two are fully theirs.
    assert result["distinct_query_count"] == 2
    assert result["parts"]["authorship"]["value"] == 100
    files["blame"] = [[1, 4, None]]  # authors not linked to GitHub accounts: unknown, not guessed
    result = depth(repo("r", {"q.py": files}))
    assert result["distinct_query_count"] == 3
    assert result["parts"]["authorship"]["value"] == 0
    assert "unknown line authorship" in result["parts"]["authorship"]["reason"]


def test_earlier_source_exclusion_applies_to_every_query_in_the_file() -> None:
    rejected = [{"kind": "earlier_source", "claim": {"skill_id": "python", "repo": "r", "file_path": "q.py"}}]
    result = depth(repo("r", {"q.py": source(QUERIES)}), rejected=rejected)
    assert result["distinct_query_count"] == 0
    assert [item["kind"] for item in result["skipped"]] == ["earlier_source"] * 3
    # Older passports stored the same outcome as "copied".
    rejected[0]["kind"] = "copied"
    assert depth(repo("r", {"q.py": source(QUERIES)}), rejected=rejected)["distinct_query_count"] == 0


@pytest.mark.parametrize("caller_authored,excluded", [(False, False), (True, True)])
def test_unattributed_or_excluded_python_files_cannot_wire_sql(caller_authored: bool, excluded: bool) -> None:
    caller = source('def run():\n    return "query.sql"\n', authored=caller_authored)
    rejected = ([{"kind": "earlier_source", "claim": {"repo": "r", "file_path": "app.py"}}]
                if excluded else [])
    result = depth(repo("r", {"query.sql": source("SELECT id FROM users;"), "app.py": caller}), rejected=rejected)
    query = result["distinct_queries"][0]
    assert query["wired"]["status"] == "none"
    assert query["wired"]["evidence"] is None


def test_advisory_copy_matches_are_recruiter_only() -> None:
    cited = {"type": "code", "repo": "r", "file_path": "q.py", "start_line": 2, "end_line": 2,
             "integrity": [{"id": "copy_match", "label": "Earlier public source", "status": "warn",
                            "detail": "One line matches other/repo."}]}
    result = depth(repo("r", {"q.py": source(QUERIES)}), skills=[{"skill_id": "sql", "evidence": [cited]}])
    first = result["distinct_queries"][0]["evidence"][0]
    assert first["copy_check"] == "advisory_match" and first["advisory"][0]["id"] == "copy_match"
    assert result["distinct_queries"][1]["evidence"][0]["copy_check"] == "not_checked"
    student = student_sql_depth(result)
    for query in student["distinct_queries"]:
        for item in query["evidence"]:
            assert "copy_check" not in item and "advisory" not in item
    assert "copy_check" in result["distinct_queries"][0]["evidence"][0]  # the stored result is unchanged


# --- Project context ---------------------------------------------------------------------------


def test_integration_rewards_wired_queries_workflows_and_relationships() -> None:
    result = depth(*rich_repos())
    def wired(prefix: str) -> dict:
        return next(q["wired"] for q in result["distinct_queries"] if q["shape"].startswith(prefix))

    assert wired("INSERT INTO projects")["status"] == "route"
    joined = wired("SELECT t.id, t.title, p.name")  # list_tasks() is called from a route handler
    assert joined["status"] == "caller" and "from a route or command handler" in joined["detail"]
    assert joined["evidence"]["file_path"] == "app/tasks.py"
    assert wired("WITH recent AS")["evidence"]["file_path"] == "insights/api.py"
    assert all(q["wired"]["status"] != "none" for q in result["distinct_queries"])
    reason = result["parts"]["integration"]["reason"]
    assert "projects–tasks" in reason and "projects–users" in reason
    assert result["parts"]["integration"]["value"] == 100


def test_queries_not_reached_from_application_code_earn_no_wiring_points() -> None:
    code = """
    def f(conn):
        conn.execute("SELECT id FROM tasks WHERE status = ?")

    conn.execute("SELECT id FROM users")
    """
    result = depth(repo("r", {"r.py": source(code)}))
    assert {q["wired"]["status"] for q in result["distinct_queries"]} == {"none"}
    assert result["parts"]["integration"]["value"] == 0


def test_a_workflow_needs_distinct_operations_on_one_table() -> None:
    code = """
    @app.post("/a")
    def a(conn):
        conn.execute("INSERT INTO tasks (title) VALUES (?)")
        conn.execute("SELECT id FROM tasks")
    """
    result = depth(repo("r", {"r.py": source(code)}))
    assert result["parts"]["integration"]["value"] == 40 + 15
    assert "r/tasks (insert/select)" in result["parts"]["integration"]["reason"]


def test_same_table_operations_in_separate_repositories_are_not_a_workflow() -> None:
    result = depth(
        repo("writer", {"write.sql": source("INSERT INTO users (id) VALUES (1);")}),
        repo("reader", {"read.sql": source("SELECT id FROM users;")}),
    )
    integration = result["parts"]["integration"]
    assert integration["value"] == 0
    assert "connected workflows" not in integration["reason"]


# --- Tests -------------------------------------------------------------------------------------


def test_testing_is_the_share_of_queries_in_files_targeted_by_committed_tests() -> None:
    result = depth(*rich_repos())
    assert result["parts"]["testing"]["value"] == 100
    reason = result["parts"]["testing"]["reason"]
    assert "not that they pass" in reason and "correctness is unverified" in reason
    reports = next(q for q in result["distinct_queries"] if "GROUP BY status" in q["shape"])
    assert reports["test"] == {"path": "tests/test_reports.py", "match": "name", "ci": "passed"}
    tasks = next(q for q in result["distinct_queries"] if q["shape"].startswith("INSERT INTO projects"))
    assert tasks["test"]["ci"] == "not_recorded"
    assert all(q["correctness"] == "unverified" for q in result["distinct_queries"])


def test_tests_written_by_someone_else_do_not_count() -> None:
    repos = rich_repos()
    repos[0]["test_authored"] = {"tests/test_tasks.py": False}
    repos[1]["test_authored"] = {"tests/test_reports.py": False}
    assert depth(*repos)["parts"]["testing"]["value"] == 0


# --- Components, caps and labels ---------------------------------------------------------------


def test_complexity_is_the_mean_of_the_top_three_distinct_levels() -> None:
    result = depth(*rich_repos())
    levels = sorted((q["level"] for q in result["distinct_queries"]), reverse=True)[:3]
    assert result["parts"]["complexity"]["value"] == round(sum(levels) / 3, 1)
    assert {q["level"] for q in result["distinct_queries"]} == {25, 50, 75, 100}


def test_breadth_counts_distinct_concepts_out_of_eight() -> None:
    result = depth(repo("r", {"q.py": source(QUERIES)}))
    found = {item["id"] for item in result["concepts_found"]}
    assert found == {"filter_sort", "group_aggregate"}
    assert result["parts"]["breadth"]["value"] == 25
    assert len(result["concepts_missing"]) == 6


def test_repetition_is_capped_when_all_queries_are_in_one_repository() -> None:
    code = "def f(conn):\n" + "".join(
        f'    conn.execute("SELECT id FROM t{index}")\n' for index in range(7)
    )
    result = depth(repo("r", {"r.py": source(code)}))
    assert result["distinct_query_count"] == 7
    assert result["parts"]["repetition"]["value"] == 75
    assert "one repository" in result["parts"]["repetition"]["reason"]


def test_one_distinct_query_caps_the_score_at_35() -> None:
    query = """
    WITH recent AS (SELECT p.name, t.status FROM tasks t JOIN projects p ON p.id = t.project_id)
    SELECT name, status, COUNT(*), RANK() OVER (PARTITION BY name ORDER BY COUNT(*) DESC) FROM recent GROUP BY name, status
    """
    code = (
        "@app.get('/r')\ndef report(conn):\n    conn.execute(\"\"\"" + textwrap.dedent(query).strip()
        + "\"\"\")\n    conn.execute(\"BEGIN\")\n"
    )
    result = depth(repo("r", {"r.py": source(code)}))
    assert result["distinct_query_count"] == 1
    assert result["score"] == 35
    assert "capped at 35" in result["cap_reason"]
    assert result["label"] == "Limited evidence"


def test_fewer_than_three_distinct_queries_is_limited_evidence() -> None:
    code = 'def f(c):\n    c.execute("SELECT id FROM a")\n    c.execute("SELECT id FROM b")\n'
    result = depth(repo("r", {"r.py": source(code)}))
    assert result["label"] == "Limited evidence"
    assert "Fewer than 3" in result["cap_reason"]


def test_labels_follow_the_bands_after_caps() -> None:
    assert [_label(score, 3) for score in (0, 34, 35, 59, 60, 79, 80, 100)] == [
        "Basic", "Basic", "Developing", "Developing", "Solid", "Solid", "Strong", "Strong",
    ]
    assert _label(95, 2) == "Limited evidence"


def test_no_sql_is_reported_as_missing_evidence() -> None:
    result = depth(repo("web", {"index.py": source("print('hi')\n")}))
    assert result["score"] == 0 and result["label"] == "Limited evidence"
    assert "No countable SQL" in result["cap_reason"]
    assert depth()["distinct_query_count"] == 0  # no repositories at all


def test_select_without_a_table_is_not_a_query() -> None:
    result = depth(repo("r", {"r.py": source('def f(c):\n    c.execute("SELECT 1")\n')}))
    assert result["distinct_query_count"] == 0 and result["skipped"][0]["kind"] == "no_table"


def test_unexpected_errors_never_fail_the_passport(monkeypatch) -> None:
    from app.services import sql_depth

    def broken(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(sql_depth, "compute_sql_depth", broken)
    assert safe_sql_depth(ingestion(), {"skills": [], "rejected_claims": []}) is None


# --- Pipeline, flag and regressions ------------------------------------------------------------


def _stub_pipeline(monkeypatch, repos: list[dict], skills: list[dict]) -> None:
    monkeypatch.setattr(pipeline, "get_passport", lambda _: None)
    monkeypatch.setattr(
        pipeline, "ingest_github",
        lambda username, force_refresh=False: {"username": username, "repo_count": len(repos), "repos": repos},
    )
    monkeypatch.setattr(pipeline, "parse_resume", lambda _: {"skills": [], "github_logins": []})
    monkeypatch.setattr(pipeline, "propose_claims", lambda *_: [])
    monkeypatch.setattr(
        pipeline, "verify_claims", lambda *_a, **_k: {"skills": copy.deepcopy(skills), "rejected_claims": []},
    )
    monkeypatch.setattr(pipeline, "add_copy_signals", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "generate_growth_and_kit", lambda *_: {"growth_tasks": [], "interview_kit": []})
    monkeypatch.setattr(pipeline, "save_passport", lambda _: None)


def _build() -> dict:
    return pipeline.build_passport(
        github_username="student", target_role="backend-intern", resume_bytes=b"%PDF", is_demo=True,
    )


SKILLS = [{"skill_id": "sql", "name": "SQL", "level": "Used", "certified": False,
           "evidence": [{"type": "dependency", "checks": {"authored": True}}]}]


def test_pipeline_computes_sql_depth_without_changing_levels_or_the_match(monkeypatch) -> None:
    _stub_pipeline(monkeypatch, rich_repos(), SKILLS)
    monkeypatch.setattr(config, "ENABLE_SQL_DEPTH", True)
    monkeypatch.setattr(config, "ENABLE_SKILL_DEPTH", True)
    on = _build()
    monkeypatch.setattr(config, "ENABLE_SQL_DEPTH", False)
    off = _build()
    assert on["sql_depth"]["score"] >= 70
    assert "sql_depth" not in off
    assert on["skill_depth"]["sql"]["score"] == on["sql_depth"]["score"]
    assert "sql" not in off["skill_depth"]
    for key in ("skills", "role_match", "rejected_claims", "verifier_version"):
        assert on[key] == off[key]
    assert on["skills"][0]["level"] == "Used"
    assert on["skill_depth"]["sql"]["score"] == on["sql_depth"]["score"]


def test_flag_off_hides_stored_results_and_the_feature(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    stored = {"id": "p", "skills": [], "rejected_claims": [], "interview_kit": [],
              "sql_depth": depth(*rich_repos())}
    monkeypatch.setattr(config, "ENABLE_SQL_DEPTH", True)
    assert "sql_depth" in main_module.student_view(stored)
    monkeypatch.setattr(main_module, "list_review_requests", lambda _id: [])
    monkeypatch.setattr(main_module, "requirement_packets", lambda _p: [])
    assert "sql_depth" in main_module.review_view(stored)
    client = TestClient(main_module.app)
    assert client.get("/features").json()["sql_depth"] is True
    monkeypatch.setattr(config, "ENABLE_SQL_DEPTH", False)
    assert "sql_depth" not in main_module.student_view(stored)
    assert "sql_depth" not in main_module.review_view(stored)
    assert "sql_depth" in stored  # stored data is untouched
    assert client.get("/features").json()["sql_depth"] is False


def test_old_passports_without_sql_depth_still_render(monkeypatch) -> None:
    monkeypatch.setattr(config, "ENABLE_SQL_DEPTH", True)
    old = {"id": "p", "skills": [], "rejected_claims": [], "interview_kit": []}
    assert "sql_depth" not in main_module.student_view(old)
    assert main_module.student_view({**old, "sql_depth": None})["sql_depth"] is None


def test_computing_sql_depth_does_not_mutate_the_evidence() -> None:
    repos = rich_repos()
    skills = [{"skill_id": "sql", "level": "Applied", "evidence": [
        {"type": "code", "repo": "task-tracker", "file_path": "app/tasks.py", "start_line": 20, "end_line": 20,
         "integrity": []},
    ]}]
    before = copy.deepcopy((repos, skills))
    compute_sql_depth(ingestion(*repos), {"skills": skills, "rejected_claims": []})
    assert (repos, skills) == before


@pytest.mark.parametrize("fixture", [basic_repos, rich_repos])
def test_scores_are_deterministic(fixture) -> None:
    assert depth(*fixture()) == depth(*fixture())
