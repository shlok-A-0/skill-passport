import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.skill_depth import PROFILES, _generic_profile, compute_skill_depth


def source(text: str, path: str) -> tuple[str, dict]:
    lines = text.strip("\n").splitlines()
    return path, {
        "content": "\n".join(lines),
        "lines": lines,
        "line_count": len(lines),
        "authored_shas": ["abc"],
        "blame": [[1, len(lines), "student"]],
    }


def repo(files: dict[str, str], *, config: dict[str, str] | None = None,
         authored_paths: dict[str, bool] | None = None, commits: list | None = None) -> dict:
    sources = dict(source(text, path) for path, text in files.items())
    return {
        "name": "demo", "full_name": "student/demo", "head_sha": "abc123",
        "is_fork": False, "tree": list(files), "source_files": sources,
        "config_contents": config or {}, "authored_paths": authored_paths or {},
        "user_commits": commits or [],
    }


def run(skill_id: str, repo_data: dict) -> dict:
    return _generic_profile(skill_id, {"username": "student", "repos": [repo_data]},
                            {"skills": [], "rejected_claims": []})


PILOT_FIXTURES = {
    "python": ("def transform(items):\n    for item in items:\n        try:\n            yield item.strip()\n        except ValueError:\n            raise\n", "src/main.py"),
    "react": ("import React, { useState, useEffect } from 'react';\n"
              "export function SearchBox({ children }) {\n"
              " const [query, setQuery] = useState('');\n"
              " useEffect(() => {}, [query]);\n"
              " return <section aria-label=\"Search\"><button onClick={() => {}}>{children}</button></section>;\n"
              "}\n", "src/SearchBox.tsx"),
    "fastapi": ("from fastapi import FastAPI, Depends, HTTPException\n"
                "from pydantic import BaseModel\napp = FastAPI()\nclass Item(BaseModel):\n name: str\n"
                "@app.post('/items')\nasync def create_item(item: Item, user=Depends(get_user)) -> Item:\n"
                " if not item.name:\n  raise HTTPException(status_code=400)\n return item\n", "app/main.py"),
    "docker": ("FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nUSER app\n"
               "HEALTHCHECK CMD python -c \"print('ok')\"\nCMD [\"python\", \"main.py\"]\n", "Dockerfile"),
    "git": ("def main():\n    return 1\n", "src/main.py"),
    "unit-testing": ("from app import calculate\n"
                     "def test_calculate():\n    assert calculate(2) == 4\n"
                     "def test_invalid():\n    with pytest.raises(ValueError):\n        calculate(-1)\n", "tests/test_calc.py"),
    "scikit-learn": ("from sklearn.model_selection import train_test_split\n"
                     "from sklearn.preprocessing import StandardScaler\n"
                     "from sklearn.pipeline import make_pipeline\n"
                     "from sklearn.metrics import accuracy_score\n"
                     "from sklearn.ensemble import RandomForestClassifier\n"
                     "X_train, X_test, y_train, y_test = train_test_split(X, y)\n"
                     "model = make_pipeline(StandardScaler(), RandomForestClassifier())\n"
                     "model.fit(X_train, y_train)\n"
                     "score = accuracy_score(y_test, model.predict(X_test))\n", "src/model.py"),
}


@pytest.mark.parametrize("skill_id,fixture", PILOT_FIXTURES.items())
def test_each_non_sql_pilot_has_cited_observations(skill_id, fixture):
    code, path = fixture
    data = repo({path: code}, commits=[{"sha": "abc"}] if skill_id == "git" else None)
    result = run(skill_id, data)
    observed = [item for item in result["competencies"] if item["status"] == "observed"]
    assert observed, skill_id
    assert all(item["evidence"][0]["url"].endswith(f"#L{item['evidence'][0]['start_line']}-L{item['evidence'][0]['end_line']}")
               for item in observed)
    assert result["rubric_version"] == "pilot-1"
    assert result["disclaimer"]


def test_all_rubrics_total_100_and_foundational_competencies_are_declared():
    assert set(PROFILES) == {"python", "react", "fastapi", "docker", "git", "unit-testing", "scikit-learn"}
    for profile in PROFILES.values():
        assert sum(item[2] for item in profile) == 100
        assert sum(item[3] for item in profile) >= 3


def test_score_gate_and_foundation_guard():
    # Three assessable competencies are enough for the gate, but 60% weight is also required.
    small = repo({"src/a.py": "def f():\n    return 1\n"})
    result = run("python", small)
    assert result["coverage"]["assessable_competencies"] >= 3
    assert result["score"] is not None
    assert result["label"] != "Broad evidence"
    assert result["score"] == 100 * sum(row["weight"] for row in result["competencies"]
                                        if row["status"] == "observed") // result["coverage"]["assessable_weight"]


def test_unattributed_and_forked_files_are_insufficient_not_zero():
    fork = repo({"src/a.py": "def f():\n    return 1\n"})
    fork["is_fork"] = True
    result = run("python", fork)
    assert result["score"] is None
    assert result["label"] == "Insufficient evidence"
    assert all(row["status"] == "insufficient_evidence" for row in result["competencies"])

    unattributed = repo({"src/a.py": "def f():\n    return 1\n"})
    unattributed["source_files"]["src/a.py"]["blame"] = None
    result = run("python", unattributed)
    structure = next(row for row in result["competencies"] if row["id"] == "structure")
    assert structure["status"] == "insufficient_evidence"
    assert structure["evidence"] == []


def test_not_observed_is_not_presented_as_missing_skill():
    data = repo({"src/a.py": "x = 1\n"})
    result = run("python", data)
    assert "not_observed" in {row["status"] for row in result["competencies"]}
    assert all("does not mean" in row["explanation"] for row in result["competencies"]
               if row["status"] == "not_observed")


def test_repeated_matches_are_capped_and_exact_citations_are_pinned():
    code = "def one():\n    return 1\n\ndef two():\n    return 2\n"
    result = run("python", repo({"src/a.py": code}))
    structure = next(row for row in result["competencies"] if row["id"] == "structure")
    assert len(structure["evidence"]) == 1
    assert structure["points"] == structure["weight"]
    assert "abc123" in structure["evidence"][0]["url"]


def test_bad_python_is_not_treated_as_zero_signal():
    result = run("python", repo({"src/a.py": "def incomplete(:\n pass\n"}))
    assert result["score"] is None
    assert all(row["status"] == "insufficient_evidence" for row in result["competencies"])


def test_react_profile_requires_react_project_context():
    data = repo({"src/Widget.tsx": "export function Widget() { return <div />; }\n"})
    result = run("react", data)
    assert result["score"] is None
    assert all(row["status"] == "insufficient_evidence" for row in result["competencies"])


def test_react_javascript_files_with_jsx_are_analyzed():
    code = "import React from 'react';\nexport function Widget() { return <button onClick={() => {}}>Go</button>; }\n"
    result = run("react", repo({"src/Widget.js": code}))
    components = next(row for row in result["competencies"] if row["id"] == "components")
    assert components["status"] == "observed"
    assert components["evidence"][0]["file_path"] == "src/Widget.js"


def test_react_project_context_allows_automatic_jsx_runtime():
    data = repo({"src/Widget.js": "export function Widget() { return <button>Go</button>; }\n"})
    data["dependency_contents"] = {"package.json": '{"dependencies":{"react":"^19"}}'}
    result = run("react", data)
    components = next(row for row in result["competencies"] if row["id"] == "components")
    assert components["status"] == "observed"


def test_javascript_unit_tests_are_insufficient_until_a_parser_exists():
    code = "import { describe, it, expect } from 'vitest';\ndescribe('x', () => it('works', () => expect(1).toBe(1)));\n"
    result = run("unit-testing", repo({"tests/example.test.js": code}))
    assert result["score"] is None
    assert all(row["status"] == "insufficient_evidence" for row in result["competencies"])


def test_missing_line_blame_cannot_establish_zero_depth_or_coverage():
    data = repo({"src/a.py": "x = 1\n"})
    data["source_files"]["src/a.py"]["blame"] = None
    result = run("python", data)
    assert result["score"] is None
    assert result["coverage"]["percent"] == 0
    assert all(row["status"] == "insufficient_evidence" for row in result["competencies"])


def test_file_with_no_current_candidate_lines_cannot_establish_zero_depth_or_coverage():
    data = repo({"src/a.py": "x = 1\n"})
    data["source_files"]["src/a.py"]["blame"] = [[1, 1, "collaborator"]]
    result = run("python", data)
    assert result["score"] is None
    assert result["coverage"]["percent"] == 0
    assert all(row["status"] == "insufficient_evidence" for row in result["competencies"])


def test_fastapi_profile_ignores_flask_decorators_and_async_handlers():
    code = "from flask import Flask\napp = Flask(__name__)\n@app.get('/')\nasync def route():\n    try:\n        return {}\n    except Exception:\n        return {}\n"
    result = run("fastapi", repo({"app.py": code}))
    assert result["score"] is None
    assert not any(row["status"] == "observed" for row in result["competencies"])


@pytest.mark.parametrize("module_import", ["import fastapi", "import fastapi as fa"])
def test_fastapi_module_import_forms_are_recognized(module_import):
    namespace = "fastapi" if module_import == "import fastapi" else "fa"
    code = f"{module_import}\napp = {namespace}.FastAPI()\n@app.get('/')\ndef route(): return {{}}\n"
    result = run("fastapi", repo({"app.py": code}))
    routes = next(row for row in result["competencies"] if row["id"] == "routes")
    assert routes["status"] == "observed"


def test_scikit_profile_ignores_unrelated_classifier_and_fit_names():
    code = "class Classifier:\n    def fit(self, values): return self\nmodel = Classifier()\nmodel.fit(data)\n"
    result = run("scikit-learn", repo({"model.py": code}))
    assert result["score"] is None
    assert not any(row["status"] == "observed" for row in result["competencies"])


@pytest.mark.parametrize("code", [
    "from sklearn.linear_model import LogisticRegression as Model\nmodel = Model()\nmodel.fit(X, y)\nmodel.predict(X)\n",
    "import sklearn.linear_model\nmodel = sklearn.linear_model.LinearRegression()\nmodel.fit(X, y)\nmodel.predict(X)\n",
    "from sklearn.svm import SVC\nmodel = SVC()\nmodel.fit(X, y)\nmodel.predict(X)\n",
])
def test_common_sklearn_estimators_are_resolved_by_import_path(code):
    result = run("scikit-learn", repo({"model.py": code}))
    estimator = next(row for row in result["competencies"] if row["id"] == "estimator")
    assert estimator["status"] == "observed"


def test_fully_authored_signal_counts_inside_a_shared_file():
    code = "".join("# shared context\n" for _ in range(5))
    code += "def transform(items):\n    for item in items:\n        yield item.strip()\n"
    code += "".join("# surrounding collaborator code\n" for _ in range(8))
    data = repo({"src/shared.py": code})
    line_count = len(data["source_files"]["src/shared.py"]["lines"])
    data["source_files"]["src/shared.py"]["blame"] = [
        [1, 5, "collaborator"], [6, 8, "student"], [9, line_count, "collaborator"],
    ]
    result = run("python", data)
    structure = next(row for row in result["competencies"] if row["id"] == "structure")
    assert structure["status"] == "observed"
    assert structure["evidence"][0]["start_line"] == 6


def test_docker_compose_only_counts_when_authored():
    compose = "services:\n  app:\n    image: demo\n"
    data = repo({}, config={"compose.yaml": compose}, authored_paths={"compose.yaml": True})
    result = run("docker", data)
    compose_row = next(row for row in result["competencies"] if row["id"] == "compose")
    assert compose_row["status"] == "observed"
    assert compose_row["evidence"][0]["url"].endswith("compose.yaml#L1-L1")


def test_unit_testing_targeting_requires_a_reference_to_an_analyzed_source():
    data = repo({
        "src/calc.py": "def calculate(value):\n    return value * 2\n",
        "tests/test_calc.py": "from calc import calculate\n\ndef test_calculate():\n    assert calculate(2) == 4\n",
    })
    result = run("unit-testing", data)
    targeting = next(row for row in result["competencies"] if row["id"] == "targeting")
    assert targeting["status"] == "observed"
    assert targeting["evidence"][0]["file_path"] == "tests/test_calc.py"


def row(result: dict, competency: str) -> dict:
    return next(item for item in result["competencies"] if item["id"] == competency)


@pytest.mark.parametrize("code", [
    "def totals(rows):\n    return {row.id: row.total for row in rows}\n",
    "def ids(rows):\n    return [row.id for row in rows]\n",
    "def tags(rows):\n    return {row.tag for row in rows}\n",
    "def total(rows):\n    return sum(row.total for row in rows)\n",
    "def digits(number):\n    while number:\n        number //= 10\n",
    "def ranked(rows):\n    return sorted(rows, key=len)\n",
])
def test_python_data_handling_includes_comprehensions_while_and_sorted(code):
    data = row(run("python", repo({"src/a.py": code})), "data")
    assert data["status"] == "observed"
    assert data["evidence"][0]["start_line"] == 2


@pytest.mark.parametrize("code,observed", [
    ("from pydantic import BaseModel\n\nclass Request(BaseModel):\n    url: str\n", False),
    ("class ShortenerError(ValueError):\n    \"\"\"Raised when a URL can't be shortened.\"\"\"\n", False),
    ("class Store:\n    def add(self, item):\n        return item\n", True),
    ("from typing import Protocol\n\nclass Reader(Protocol):\n    name: str\n", True),
    ("import abc\n\nclass Base(abc.ABC):\n    name: str\n", True),
    ("from dataclasses import dataclass\n\n@dataclass\nclass Point:\n    x: int\n", True),
])
def test_python_interfaces_need_a_method_or_an_explicit_interface(code, observed):
    interfaces = row(run("python", repo({"src/a.py": code})), "interfaces")
    assert (interfaces["status"] == "observed") is observed


def test_python_robustness_ignores_asserts_and_never_reuses_the_error_handling_raise():
    asserts = "def check(value):\n    assert value > 0\n    return value\n"
    assert row(run("python", repo({"src/a.py": asserts})), "robustness")["status"] == "not_observed"

    one_raise = "def check(value):\n    if value < 0:\n        raise ValueError('negative')\n    return value\n"
    result = run("python", repo({"src/a.py": one_raise}))
    assert row(result, "errors")["evidence"][0]["start_line"] == 3
    assert row(result, "robustness")["status"] == "not_observed"

    two_raises = one_raise.replace("    return value\n", "    if value > 9:\n        raise ValueError('big')\n    return value\n")
    result = run("python", repo({"src/a.py": two_raises}))
    assert row(result, "errors")["evidence"][0]["start_line"] == 3
    assert row(result, "robustness")["evidence"][0]["start_line"] == 5

    isinstance_check = "def check(value):\n    return isinstance(value, int)\n"
    assert row(run("python", repo({"src/a.py": isinstance_check})), "robustness")["status"] == "observed"


@pytest.mark.parametrize("decorator,header", [
    ("@pytest.fixture", "import pytest\n"),
    ("@pytest.fixture(scope='module')", "import pytest\n"),
    ("@fixture", "from pytest import fixture\n"),
    ("@fixture()", "from pytest import fixture\n"),
])
def test_unit_testing_isolation_detects_fixture_decorators(decorator, header):
    code = f"{header}\n{decorator}\ndef engine():\n    return object()\n\ndef test_engine(engine):\n    assert engine\n"
    isolation = row(run("unit-testing", repo({"tests/test_engine.py": code})), "isolation")
    assert isolation["status"] == "observed"
    assert isolation["evidence"][0]["start_line"] == 3  # the decorator line


def test_unit_testing_targeting_ignores_pytest_stdlib_and_package_imports():
    test = ("import pytest\nimport json\nfrom datetime import date\nimport requests\n\n"
            "def test_date():\n    assert date(2026, 1, 1)\n")
    data = repo({"app/store.py": "def add(x):\n    return x\n", "tests/test_dates.py": test})
    assert row(run("unit-testing", data), "targeting")["status"] == "not_observed"

    test = "import pytest\n\nfrom app.store import add\n\ndef test_add():\n    assert add(1) == 1\n"
    data = repo({"app/store.py": "def add(x):\n    return x\n", "tests/test_store.py": test})
    targeting = row(run("unit-testing", data), "targeting")
    assert targeting["status"] == "observed"
    assert targeting["evidence"][0]["start_line"] == 3  # the own-module import, not `import pytest`


def test_unit_testing_targeting_only_counts_the_candidates_own_modules():
    test = "from app.store import add\n\ndef test_add():\n    assert add(1) == 1\n"
    data = repo({"app/store.py": "def add(x):\n    return x\n", "tests/test_store.py": test})
    data["source_files"]["app/store.py"]["authored_shas"] = []
    assert row(run("unit-testing", data), "targeting")["status"] == "not_observed"


def test_empty_files_are_never_cited():
    data = repo({"app/__init__.py": "\n", "app/store.py": "class Store:\n    def add(self, x):\n        return x\n"},
                commits=[{"sha": "abc"}])
    data["source_files"]["app/__init__.py"]["lines"] = [""]
    for skill_id in ("python", "git"):
        cited = {item["file_path"] for competency in run(skill_id, data)["competencies"]
                 for item in competency["evidence"]}
        assert "app/__init__.py" not in cited, skill_id


def test_git_cites_file_content_not_a_line_one_placeholder():
    data = repo({"queries.sql": "\n-- totals\nSELECT 1;\n", "one.py": "x = 1\n",
                 "app.py": "def f():\n    return 1\n"}, commits=[{"sha": "abc"}])
    data["source_files"]["queries.sql"]["lines"] = ["", "-- totals", "SELECT 1;"]
    result = run("git", data)
    spans = {(item["file_path"], item["start_line"], item["end_line"])
             for competency in result["competencies"] for item in competency["evidence"]}
    assert ("queries.sql", 2, 3) in spans
    assert ("app.py", 1, 2) in spans
    assert not any(path == "one.py" for path, _start, _end in spans)  # only line 1 has content


def test_vite_scaffold_entry_point_is_not_composition_evidence():
    scaffold = ("import { StrictMode } from 'react';\nimport { createRoot } from 'react-dom/client';\n"
                "import App from './App';\n\ncreateRoot(document.getElementById('root')!).render(\n"
                "  <StrictMode>\n    <App />\n  </StrictMode>,\n);\n")
    assert row(run("react", repo({"src/main.tsx": scaffold})), "composition")["status"] == "not_observed"

    routed = scaffold.replace("    <App />\n", "    <BrowserRouter>\n      <App />\n    </BrowserRouter>\n")
    composition = row(run("react", repo({"src/main.tsx": routed})), "composition")
    assert composition["status"] == "observed"


def test_git_uses_author_attribution_but_not_commit_volume():
    authored = repo({"src/a.py": "def f():\n    return 1\n"}, commits=[{"sha": "abc"}])
    one = run("git", authored)
    authored["user_commits"] = [{"sha": str(i)} for i in range(100)]
    many = run("git", authored)
    assert one["score"] == many["score"]


def test_sql_adapter_retains_existing_result_and_result_errors_are_isolated(monkeypatch):
    from app.services import skill_depth

    legacy = {
        "score": 42, "label": "Developing", "parts": {
            "complexity": {"label": "Complexity", "weight": .2, "points": 8, "reason": "existing", "evidence": []}
        }, "note": "legacy disclaimer",
    }
    monkeypatch.setattr(skill_depth, "safe_sql_depth", lambda *_: legacy)
    results = compute_skill_depth({"username": "student", "repos": []}, {"skills": [], "rejected_claims": []})
    assert results["sql"]["score"] == 42
    assert results["sql"]["label"] == "Developing"

    monkeypatch.setattr(skill_depth, "_generic_profile", lambda skill, *_: (_ for _ in ()).throw(ValueError())
                        if skill == "react" else {"score": None, "coverage": {}})
    results = compute_skill_depth({"username": "student", "repos": []}, {"skills": [], "rejected_claims": []}, legacy)
    assert results["react"] is None
    assert results["python"]["score"] is None


def test_student_view_redacts_depth_advisories_without_mutating_stored_passport(monkeypatch):
    from app import config, main
    from app.services.skill_depth import student_skill_depth

    monkeypatch.setattr(config, "ENABLE_SKILL_DEPTH", True)
    saved = {"sql": {"score": 22, "label": "Limited evidence", "rubric_version": "sql-legacy",
                     "coverage": {}, "competencies": [{"id": "x", "evidence": [{
                         "repo": "demo", "file_path": "x.py", "start_line": 1, "end_line": 1,
                         "url": "https://github.com/student/demo/blob/abc/x.py#L1-L1",
                         "integrity": [{"id": "copy", "status": "warn"}],
                     }]}]}}
    view = student_skill_depth(saved)
    assert "integrity" not in view["sql"]["competencies"][0]["evidence"][0]
    assert "integrity" in saved["sql"]["competencies"][0]["evidence"][0]
    stored = {"id": "p", "skills": [], "rejected_claims": [], "interview_kit": [], "skill_depth": saved}
    assert "skill_depth" in main.student_view(stored)
    monkeypatch.setattr(config, "ENABLE_SQL_DEPTH", False)
    student = main.student_view(stored)
    assert "sql" not in student["skill_depth"]
    monkeypatch.setattr(main, "list_review_requests", lambda _id: [])
    monkeypatch.setattr(main, "requirement_packets", lambda _passport: [])
    recruiter = main.review_view(stored)
    assert "sql" not in recruiter["skill_depth"]
    assert "sql" in stored["skill_depth"]
    monkeypatch.setattr(config, "ENABLE_SKILL_DEPTH", False)
    assert "skill_depth" not in main.student_view(stored)
    assert "skill_depth" not in main.review_view(stored)
    assert "skill_depth" in stored


def test_features_lists_only_enabled_depth_profiles(monkeypatch):
    from app import config, main

    monkeypatch.setattr(config, "ENABLE_SKILL_DEPTH", True)
    feature_response = main.features()
    assert feature_response["skill_depth"] is True
    assert feature_response["skill_depth_profiles"] == [
        "sql", "python", "react", "fastapi", "docker", "git", "unit-testing", "scikit-learn"
    ]
    monkeypatch.setattr(config, "ENABLE_SKILL_DEPTH", False)
    assert main.features()["skill_depth_profiles"] == []

    monkeypatch.setattr(config, "ENABLE_SKILL_DEPTH", True)
    monkeypatch.setattr(config, "ENABLE_SQL_DEPTH", False)
    assert "sql" not in main.features()["skill_depth_profiles"]
