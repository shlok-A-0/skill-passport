import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import jd_parse
from app.services.framework import skill_map
from app.services.role_match import match_role
from app.services.verifier import (
    STRING_SIGNATURE_SKILLS,
    _signature_found,
    compute_level,
    reachable_min_level,
)

# expense-tracker-api/app/db.py lines 45-55 (the citation rejected on passport 68838ca4 was L50-L53).
DB_PY = '''from datetime import date

from sqlalchemy import Engine, text


def monthly_totals(engine: Engine, year: int, month: int) -> dict[str, int]:
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT category, SUM(amount_cents) AS total FROM expenses "
                 "WHERE spent_on >= :start AND spent_on < :end "
                 "GROUP BY category ORDER BY total DESC"),
            {"start": start, "end": end},
        )
        return {row.category: int(row.total) for row in rows}
'''


def _found(skill_id: str, source: str, start: int, end: int, path: str = "app/db.py") -> bool:
    return _signature_found(skill_map()[skill_id], path, source.splitlines(), start, end)


def test_sql_inside_text_call_passes() -> None:
    assert _found("sql", DB_PY, 11, 14)


@pytest.mark.parametrize("source", [
    'def save(cursor, name):\n    cursor.execute("INSERT INTO users (name) VALUES (%s)", (name,))\n',
    'import pandas as pd\n\ndef load(conn):\n    return pd.read_sql("SELECT * FROM orders", conn)\n',
    'def load(cursor, table):\n    cursor.execute("SELECT id FROM " + table)\n',
    'def make(conn):\n    conn.executescript(\n        """CREATE TABLE notes (\n            id INTEGER PRIMARY KEY\n        )"""\n    )\n',
])
def test_sql_in_other_call_arguments_passes(source: str) -> None:
    lines = source.splitlines()
    assert _found("sql", source, 1, len(lines))


def test_sql_only_in_a_comment_fails() -> None:
    source = 'def total(conn):\n    # SELECT SUM(amount) FROM expenses\n    return conn.total()\n'
    assert not _found("sql", source, 1, 3)


def test_sql_only_in_a_docstring_fails() -> None:
    source = 'def total(conn):\n    """Runs SELECT SUM(amount) FROM expenses."""\n    return conn.total()\n'
    assert not _found("sql", source, 1, 3)


def test_sql_in_a_bare_string_that_is_not_a_call_argument_fails() -> None:
    # Narrow by design: only strings passed to a call count.
    source = 'QUERY = "SELECT * FROM expenses"\n\ndef total(conn):\n    return conn.total()\n'
    assert not _found("sql", source, 1, 4)


@pytest.mark.parametrize(("skill_id", "call", "comment"), [
    ("postgresql", 'conn = psycopg2.connect("postgresql://app@db/expenses")', "# postgresql://app@db/expenses"),
    ("mysql", 'engine = make("mysql://root@localhost/shop")', "# mysql://root@localhost/shop"),
    ("mongodb", 'client = connect("mongodb://localhost:27017")', "# mongodb://localhost:27017"),
    ("redis", 'cache = from_url("redis://localhost:6379/0")', "# redis://localhost:6379/0"),
    ("docker", 'subprocess.run("docker build -t app .", shell=True)', "# docker build -t app ."),
])
def test_other_string_borne_skills_accept_call_arguments_only(skill_id: str, call: str, comment: str) -> None:
    assert skill_id in STRING_SIGNATURE_SKILLS
    assert _found(skill_id, f"def setup():\n    {call}\n", 1, 2, path="app/setup.py")
    assert not _found(skill_id, f"def setup():\n    {comment}\n    return None\n", 1, 3, path="app/setup.py")
    assert not _found(skill_id, f'def setup():\n    """{comment[2:]}"""\n    return None\n', 1, 3, path="app/setup.py")


def test_other_skills_still_ignore_strings() -> None:
    assert "flask" not in STRING_SIGNATURE_SKILLS
    source = 'def show():\n    print("from flask import Flask")\n'
    assert not _found("flask", source, 1, 2, path="app/show.py")


def test_sql_in_javascript_strings_is_still_not_evidence() -> None:
    # Only Python call arguments were changed; .js is not an allowed SQL extension.
    assert not _found("sql", 'db.query("SELECT * FROM users");\n', 1, 1, path="src/db.js")


def _repo_with_one_test_file() -> dict:
    test_source = "from app.engine import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    return {
        "name": "demo",
        "source_files": {"tests/test_engine.py": {"lines": test_source.splitlines(), "content": test_source,
                                                   "authored_shas": ["abc"]}},
        "test_files": ["tests/test_engine.py"],
        "test_authored": {"tests/test_engine.py": True},
        "test_contents": {"tests/test_engine.py": test_source},
        "config_files": [],
    }


def test_unit_testing_evidence_stops_at_applied() -> None:
    repo = _repo_with_one_test_file()
    citation = {"type": "code", "repo": "demo", "file_path": "tests/test_engine.py", "start_line": 4, "end_line": 5}
    assert compute_level("unit-testing", [citation], [repo]) == "Applied"
    # The same test does make the code it tests Demonstrated.
    code = {"type": "code", "repo": "demo", "file_path": "app/engine.py", "start_line": 1, "end_line": 3}
    assert compute_level("python", [code], [repo]) == "Demonstrated"


def test_required_levels_are_capped_at_what_the_skill_can_reach() -> None:
    assert reachable_min_level("unit-testing", "Demonstrated") == "Applied"
    assert reachable_min_level("unit-testing", "Used") == "Used"
    assert reachable_min_level("python", "Demonstrated") == "Demonstrated"


def test_job_description_testing_words_do_not_raise_unit_testing_itself(monkeypatch) -> None:
    monkeypatch.setattr(jd_parse, "propose_json_items", lambda *_a, **_k: [])
    text = "Requirements\n- Unit tests with pytest are required.\n- Python code must be well tested."
    requirements = {item["skill_id"]: item["min_level"] for item in jd_parse.extract_requirements("Intern", text)["requirements"]}
    assert requirements["unit-testing"] == "Applied"
    assert requirements["python"] == "Demonstrated"  # other skills still use the testing-word rule


def test_matching_caps_stored_custom_roles_and_leaves_fixed_roles_unchanged() -> None:
    skills = [{"skill_id": "unit-testing", "level": "Applied", "evidence": [{"type": "code"}]}]
    stored_role = {"id": "custom-old", "name": "Old custom role", "requirements": [
        {"skill_id": "unit-testing", "importance": "must", "min_level": "Demonstrated"}]}
    [requirement] = match_role(skills, stored_role)["requirements"]
    assert (requirement["status"], requirement["min_level"]) == ("met", "Applied")
    fixed = next(item for item in match_role(skills, "backend-intern")["requirements"] if item["skill_id"] == "unit-testing")
    assert (fixed["status"], fixed["min_level"]) == ("met", "Applied")
