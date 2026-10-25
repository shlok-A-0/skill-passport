"""Synthetic fixture repositories with agent-authored labels.

Label provenance and limits (applies to every fixture below):
- Labels were authored by the Instinct task agent on 2026-10-25 from this
  repository's own taxonomy (app/data/skills.json) and the documented
  Evidence Ladder rules (app/services/verifier.py). No independent human
  review has occurred. Labels encode what a careful reviewer applying the
  project's own rules would conclude about each fixture repository.
- Fixtures measure defined detection and level-assignment behaviors of the
  deterministic stages plus file selection. They do not measure the Gemini
  claim-proposal step (replaced by a documented stand-in) and they are not
  real-person proficiency ground truth.
- Split: "tune" fixtures may guide changes; "heldout" fixtures are reported
  but never used to tune, limiting (not eliminating) design bias, since one
  author wrote both the fixtures and the code under test.
"""
from __future__ import annotations

from typing import Any

LABELED_BY = (
    "Instinct task agent, 2026-10-25, from app/data/skills.json and the "
    "Evidence Ladder rules in app/services/verifier.py; no independent human review"
)

# ---------------------------------------------------------------------------
# Repository builders
# ---------------------------------------------------------------------------

FLASK_APP = """from flask import Flask

app = Flask(__name__)


@app.route('/health')
def health():
    return {'status': 'ok'}
"""

ENGINE = """def add(a, b):
    return a + b


def divide(a, b):
    if b == 0:
        raise ValueError("b must not be zero")
    return a / b
"""

ENGINE_TEST = """from engine import add, divide


def test_add():
    assert add(1, 2) == 3


def test_divide_by_zero():
    try:
        divide(1, 0)
        assert False
    except ValueError:
        assert True
"""

FASTAPI_APP = """from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
"""

UTILS = """def shout(text):
    return text.upper() + "!"
"""


def _source(content: str, *, authored: bool = True, blame: list | None = None,
            history: dict | None = None, first_commit_date: str | None = None) -> dict[str, Any]:
    lines = content.splitlines()
    source: dict[str, Any] = {
        "content": content,
        "lines": lines,
        "line_count": len(lines),
        "authored_shas": ["c1"] if authored else [],
    }
    if blame is not None:
        source["blame"] = blame
    if history is not None:
        source["history"] = history
    if first_commit_date is not None:
        source["first_commit_date"] = first_commit_date
    return source


def _repo(name: str, *, tree: list[str], source_files: dict[str, dict] | None = None,
          dependency_contents: dict[str, str] | None = None,
          config_contents: dict[str, str] | None = None,
          test_files: list[str] | None = None,
          test_authored: dict[str, bool] | None = None,
          user_commits: list[dict] | None = None,
          is_fork: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "full_name": f"candidate/{name}",
        "owner": "candidate",
        "head_sha": "head123",
        "is_fork": is_fork,
        "tree": tree,
        "source_files": source_files or {},
        "dependency_files": list((dependency_contents or {}).keys()),
        "dependency_contents": dependency_contents or {},
        "config_files": list((config_contents or {}).keys()),
        "config_contents": config_contents or {},
        "test_files": test_files or [],
        "test_authored": test_authored or {},
        "authored_paths": {
            **{path: True for path in (dependency_contents or {})},
            **{path: True for path in (config_contents or {})},
        },
        "commit_counts": {"total": 12, "by_user": 12},
        "user_commits": user_commits if user_commits is not None else [
            {"sha": "c1", "author_date": "2026-10-01T10:00:00Z"},
            {"sha": "c2", "author_date": "2026-10-05T10:00:00Z"},
            {"sha": "c3", "author_date": "2026-10-10T10:00:00Z"},
            {"sha": "c4", "author_date": "2026-10-15T10:00:00Z"},
        ],
        "user_commits_complete": True,
    }


def _ingestion(*repos: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": "candidate",
        "repos": list(repos),
        "repo_count": len(repos),
        "repos_listed": len(repos),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES: list[dict[str, Any]] = [
    {
        "id": "tune-flask-tested",
        "split": "tune",
        "purpose": "Tested Flask service reaches Demonstrated; dependency-only Docker stays Used.",
        "ingestion": _ingestion(_repo(
            "shop-api",
            tree=["engine.py", "tests/test_engine.py", "requirements.txt", "Dockerfile"],
            source_files={
                "engine.py": _source(ENGINE),
                "tests/test_engine.py": _source(ENGINE_TEST),
            },
            dependency_contents={"requirements.txt": "flask==3.0.0\npytest==8.0.0\n"},
            config_contents={"Dockerfile": "FROM python:3.11-slim\n"},
            test_files=["tests/test_engine.py"],
            test_authored={"tests/test_engine.py": True},
        )),
        "resume_skills": [{"skill_id": "flask", "mention": "Flask"}],
        "available_claims": [
            {"skill_id": "python", "repo": "shop-api", "file_path": "engine.py",
             "start_line": 1, "end_line": 8, "explanation": "Python arithmetic functions."},
        ],
        "labels": {
            "levels": {"python": "Demonstrated", "docker": "Used", "unit-testing": "Applied", "git": "Used"},
            "absent": ["kubernetes", "react"],
        },
    },
    {
        "id": "tune-unused-dependency",
        "split": "tune",
        "purpose": "A dependency listed but never imported must stay Used, never Applied.",
        "ingestion": _ingestion(_repo(
            "notes-app",
            tree=["main_app.py", "requirements.txt"],
            source_files={"main_app.py": _source(UTILS)},
            dependency_contents={"requirements.txt": "flask==3.0.0\nrequests==2.31.0\n"},
        )),
        "resume_skills": [{"skill_id": "flask", "mention": "Flask"}],
        "available_claims": [
            {"skill_id": "python", "repo": "notes-app", "file_path": "main_app.py",
             "start_line": 1, "end_line": 2, "explanation": "Python helper function."},
        ],
        "labels": {
            "levels": {"flask": "Used", "python": "Applied"},
            "absent": ["django"],
        },
    },
    {
        "id": "tune-resume-only",
        "split": "tune",
        "purpose": "A resume claim with no repository evidence stays Claimed (Unverified).",
        "ingestion": _ingestion(_repo(
            "portfolio",
            tree=["main_app.py"],
            source_files={"main_app.py": _source(UTILS)},
        )),
        "resume_skills": [
            {"skill_id": "kubernetes", "mention": "Kubernetes"},
            {"skill_id": "aws", "mention": "AWS"},
        ],
        "available_claims": [],
        "labels": {
            "levels": {"kubernetes": "Claimed", "aws": "Claimed"},
            "absent": ["terraform"],
        },
    },
    {
        "id": "tune-buried-evidence",
        "split": "tune",
        "purpose": "Evidence in a file outside the fetched sample is missed: known recall limit.",
        "ingestion": _ingestion({**_repo(
            "big-project",
            tree=["alpha.py", "beta.py", "gamma.py", "delta.py", "epsilon.py",
                  "zeta.py", "eta.py", "omega_api.py"],
        ), "all_files": {
            # All files are short (< MIN_PREFERRED_LINES) so the per-repo
            # MAX_SOURCE_FILES cap (6) applies in candidate order; omega_api.py
            # sorts 7th and is never fetched.
            "alpha.py": UTILS,
            "beta.py": UTILS,
            "gamma.py": UTILS,
            "delta.py": UTILS,
            "epsilon.py": UTILS,
            "zeta.py": UTILS,
            "eta.py": UTILS,
            "omega_api.py": FLASK_APP,
        }}),
        "resume_skills": [{"skill_id": "flask", "mention": "Flask"}],
        "available_claims": [
            # A perfect reader could make this claim, but only if omega_api.py was read.
            {"skill_id": "flask", "repo": "big-project", "file_path": "omega_api.py",
             "start_line": 1, "end_line": 8, "explanation": "Flask health endpoint."},
        ],
        "labels": {
            "levels": {"flask": "Applied"},  # ground truth about the repo; the sample may miss it
            "absent": ["django"],
        },
    },
    {
        "id": "tune-copied-blame",
        "split": "tune",
        "purpose": "Blame attributing every cited line to someone else excludes the citation.",
        "ingestion": _ingestion(_repo(
            "cloned-lib",
            tree=["engine.py"],
            source_files={
                "engine.py": _source(
                    ENGINE,
                    blame=[(1, 8, "upstream-author")],
                    history={"total": 40, "by_user": 1, "complete": True,
                             "creator_login": "upstream-author",
                             "creation": {"files_changed": 30, "total_additions": 3000}},
                    first_commit_date="2026-10-01T10:00:00Z",
                ),
            },
        )),
        "resume_skills": [],
        "available_claims": [
            {"skill_id": "python", "repo": "cloned-lib", "file_path": "engine.py",
             "start_line": 1, "end_line": 8, "explanation": "Python arithmetic functions."},
        ],
        "labels": {
            "levels": {},
            "absent": ["python"],  # citation excluded; nothing else supports the skill
        },
    },
    {
        "id": "heldout-django-postgres",
        "split": "heldout",
        "purpose": "Django project with a targeted test and a PostgreSQL dependency.",
        "ingestion": _ingestion(_repo(
            "polls",
            tree=["views.py", "tests/test_views.py", "requirements.txt"],
            source_files={
                "views.py": _source(
                    "from django.http import JsonResponse\n\n\ndef poll(request):\n"
                    "    return JsonResponse({'ok': True})\n"
                ),
                "tests/test_views.py": _source(
                    "from views import poll\n\n\ndef test_poll():\n    assert poll is not None\n"
                ),
            },
            dependency_contents={"requirements.txt": "django==5.0\npsycopg2==2.9\n"},
            test_files=["tests/test_views.py"],
            test_authored={"tests/test_views.py": True},
        )),
        "resume_skills": [],
        "available_claims": [
            {"skill_id": "django", "repo": "polls", "file_path": "views.py",
             "start_line": 1, "end_line": 5, "explanation": "Django JSON view."},
            {"skill_id": "python", "repo": "polls", "file_path": "views.py",
             "start_line": 1, "end_line": 5, "explanation": "Python view function."},
        ],
        "labels": {
            "levels": {"django": "Demonstrated", "python": "Demonstrated",
                       "postgresql": "Used", "unit-testing": "Applied"},
            "absent": ["flask"],
        },
    },
    {
        "id": "heldout-react-untested",
        "split": "heldout",
        "purpose": "React component without a targeting test reaches Applied, not Demonstrated.",
        "ingestion": _ingestion(_repo(
            "dashboard",
            tree=["src/App.tsx", "package.json"],
            source_files={
                "src/App.tsx": _source(
                    "import { useState } from 'react';\n\nexport function App() {\n"
                    "  const [count, setCount] = useState(0);\n  return <button>{count}</button>;\n}\n"
                ),
            },
            dependency_contents={"package.json": '{"dependencies": {"react": "^18.3.0", "react-dom": "^18.3.0"}}'},
        )),
        "resume_skills": [],
        "available_claims": [
            {"skill_id": "react", "repo": "dashboard", "file_path": "src/App.tsx",
             "start_line": 1, "end_line": 5, "explanation": "React component with state."},
        ],
        "labels": {
            "levels": {"react": "Applied"},
            "absent": ["vue", "unit-testing"],
        },
    },
    {
        "id": "heldout-bulk-upload",
        "split": "heldout",
        "purpose": "One-day bulk-uploaded repo: integrity signals warn; levels still follow evidence.",
        "ingestion": _ingestion(_repo(
            "weekend-project",
            tree=["engine.py", "requirements.txt"],
            source_files={
                "engine.py": _source(
                    ENGINE,
                    history={"total": 1, "by_user": 1, "complete": True,
                             "creator_login": "candidate",
                             "creation": {"files_changed": 25, "total_additions": 2500}},
                    first_commit_date="2026-10-20T10:00:00Z",
                ),
            },
            dependency_contents={"requirements.txt": "flask==3.0.0\n"},
            user_commits=[{"sha": "c1", "author_date": "2026-10-20T10:00:00Z"}],
        )),
        "resume_skills": [],
        "available_claims": [
            {"skill_id": "python", "repo": "weekend-project", "file_path": "engine.py",
             "start_line": 1, "end_line": 8, "explanation": "Python arithmetic functions."},
        ],
        "labels": {
            "levels": {"python": "Applied", "flask": "Used"},
            "absent": ["django"],
        },
    },
    {
        "id": "heldout-go-cicd",
        "split": "heldout",
        "purpose": "Go service with CI config: code claim Applied, CI Used via config.",
        "ingestion": _ingestion(_repo(
            "orders",
            tree=["main.go", ".github/workflows/ci.yml", "Dockerfile"],
            source_files={
                "main.go": _source(
                    "package main\n\nimport \"fmt\"\n\nfunc main() {\n\tfmt.Println(\"orders\")\n}\n"
                ),
            },
            config_contents={
                ".github/workflows/ci.yml": "name: ci\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
                "Dockerfile": "FROM golang:1.22\n",
            },
        )),
        "resume_skills": [],
        "available_claims": [
            {"skill_id": "go", "repo": "orders", "file_path": "main.go",
             "start_line": 1, "end_line": 7, "explanation": "Go entry point."},
        ],
        "labels": {
            "levels": {"go": "Applied", "cicd": "Used", "docker": "Used"},
            "absent": ["python"],
        },
    },
    {
        "id": "challenge-buried-evidence",
        "split": "challenge",
        "purpose": "Post-hoc challenge case built after the sampling miss was seen: "
                   "same shape as tune-buried-evidence with a different skill and repo. "
                   "Challenge cases are adversarial regressions, not independent "
                   "held-out confirmation.",
        "ingestion": _ingestion({**_repo(
            "inventory-service",
            tree=["mod_a.py", "mod_b.py", "mod_c.py", "mod_d.py", "mod_e.py",
                  "mod_f.py", "mod_g.py", "zephyr_api.py", "mod_h.py"],
        ), "all_files": {
            # All files are short so the per-repo MAX_SOURCE_FILES cap (6) applies;
            # zephyr_api.py sorts past the cap and the first pass never fetches it.
            "mod_a.py": UTILS,
            "mod_b.py": UTILS,
            "mod_c.py": UTILS,
            "mod_d.py": UTILS,
            "mod_e.py": UTILS,
            "mod_f.py": UTILS,
            "mod_g.py": UTILS,
            "zephyr_api.py": FASTAPI_APP,
            "mod_h.py": UTILS,
        }}),
        "resume_skills": [{"skill_id": "fastapi", "mention": "FastAPI"}],
        "available_claims": [
            {"skill_id": "fastapi", "repo": "inventory-service", "file_path": "zephyr_api.py",
             "start_line": 1, "end_line": 8, "explanation": "FastAPI health endpoint."},
        ],
        "labels": {
            "levels": {"fastapi": "Applied"},  # ground truth about the repo; the sample may miss it
            "absent": ["flask"],
        },
    },
    {
        "id": "challenge-buried-usage-upgrade",
        "split": "challenge",
        "purpose": "Post-hoc challenge case: flask is dependency-declared AND has buried "
                   "usage evidence. Declared-only coverage must not cap the level at Used "
                   "when bounded retrieval can still reach the usage file.",
        "ingestion": _ingestion({**_repo(
            "billing",
            tree=["part_a.py", "part_b.py", "part_c.py", "part_d.py", "part_e.py",
                  "part_f.py", "part_g.py", "yonder_api.py"],
            dependency_contents={"requirements.txt": "flask==3.0.0\n"},
        ), "all_files": {
            "part_a.py": UTILS,
            "part_b.py": UTILS,
            "part_c.py": UTILS,
            "part_d.py": UTILS,
            "part_e.py": UTILS,
            "part_f.py": UTILS,
            "part_g.py": UTILS,
            "yonder_api.py": FLASK_APP,
            "requirements.txt": "flask==3.0.0\n",
        }}),
        "resume_skills": [{"skill_id": "flask", "mention": "Flask"}],
        "available_claims": [
            {"skill_id": "flask", "repo": "billing", "file_path": "yonder_api.py",
             "start_line": 1, "end_line": 8, "explanation": "Flask health endpoint."},
        ],
        "labels": {
            "levels": {"flask": "Applied"},  # dependency alone would be Used; usage is Applied
            "absent": ["django"],
        },
    },
    {
        "id": "challenge-deep-burial",
        "split": "challenge",
        "purpose": "Post-hoc residual-limit case: the evidence file sits beyond the second "
                   "pass's fetch budget, so the miss persists. Documents the bound; the "
                   "expected mismatch is intentional.",
        "ingestion": _ingestion({**_repo(
            "monolith",
            tree=[f"chunk_{i:02d}.py" for i in range(14)] + ["xeno_api.py"],
        ), "all_files": {
            **{f"chunk_{i:02d}.py": UTILS for i in range(14)},
            "xeno_api.py": FLASK_APP,
        }}),
        "resume_skills": [{"skill_id": "flask", "mention": "Flask"}],
        "available_claims": [
            {"skill_id": "flask", "repo": "monolith", "file_path": "xeno_api.py",
             "start_line": 1, "end_line": 8, "explanation": "Flask health endpoint."},
        ],
        "labels": {
            # The bounded second pass cannot reach rank 15; Claimed is the known-wrong
            # outcome this fixture keeps visible on purpose.
            "levels": {"flask": "Applied"},
            "absent": ["django"],
        },
        "expected_persistent_miss": "flask",
    },
]
