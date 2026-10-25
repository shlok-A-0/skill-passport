"""False-positive regressions for the deterministic signature scan.

The scan cites files as code evidence (which sets the Applied level), so a weak
substring match against prose is recruiter-facing misinformation. These cases pin
the abstention rules: comments, docstrings, string literals, unused imports and
out-of-context config keys never produce citations, while genuine code use still
does. Cases demonstrated on the pre-fix implementation: a Python comment
"# select the best candidate" cited SQL, "# remember to git pull" cited git, and
a generic YAML "jobs:" key cited CI/CD.
"""

import json
from pathlib import Path

import pytest

from app.services.verifier import _signature_code_evidence

_SKILLS = {s["name"]: s for s in json.loads(Path("app/data/skills.json").read_text())}


def _scan(skill_name: str, files: dict[str, list[str]]):
    repo = {
        "name": "repo",
        "source_files": {
            path: {"lines": lines, "authored_shas": ["abc123"]}
            for path, lines in files.items()
        },
        "tree": list(files),
        "head_sha": "abc123",
        "is_fork": False,
    }
    return [(e["file_path"], e["start_line"]) for e in _signature_code_evidence(_SKILLS[skill_name], repo)]


# --- abstention cases -------------------------------------------------------

def test_sql_keyword_in_python_comment_abstains():
    assert _scan("SQL", {"app.py": ["# select the best candidate from the list", "print('hi')"]}) == []


def test_sql_keyword_in_docstring_abstains():
    files = {"mod.py": ['"""Runs SELECT queries against the warehouse nightly."""', "x = 1"]}
    assert _scan("SQL", files) == []


def test_git_mention_in_shell_comment_abstains():
    assert _scan("Git", {"deploy.sh": ["#!/bin/sh", "# remember to git pull before deploy"]}) == []


def test_git_mention_in_string_abstains():
    assert _scan("Git", {"util.py": ['msg = "remember to git pull here"', "print(msg)"]}) == []


def test_docker_cli_in_string_abstains():
    files = {"deploy.py": ['log = "now run docker build . and docker compose up"', "print(log)"]}
    assert _scan("Docker", files) == []


def test_ci_keys_outside_ci_path_abstain():
    assert _scan("CI/CD", {"settings.yml": ["jobs:", "  backend: hourly"]}) == []
    assert _scan("CI/CD", {"build.py": ["# jobs: run nightly", "x = 1"]}) == []


def test_k8s_keys_outside_yaml_abstain():
    files = {"deploy.py": ['template = "apiVersion: v1\\nkind: Deployment"', "print(template)"]}
    assert _scan("Kubernetes", files) == []


def test_unused_import_abstains():
    files = {"model.py": ["import torch", "import os", "print(os.name)"]}
    assert _scan("PyTorch", files) == []


def test_unused_from_import_abstains():
    files = {"web.py": ["from fastapi import FastAPI", "import os", "print(os.name)"]}
    # "from fastapi import" matches the import line only; FastAPI is never used.
    assert _scan("FastAPI", files) == []


def test_docker_mention_in_python_comment_abstains():
    files = {"run.py": ["import subprocess", "subprocess.run(args, shell=True)  # docker run app"]}
    assert _scan("Docker", files) == []


# --- genuine use still cites ------------------------------------------------

def test_genuine_flask_app_cites():
    files = {"app.py": ["from flask import Flask", "app = Flask(__name__)", "@app.route('/')"]}
    assert _scan("Flask", files) == [("app.py", 1)]


def test_used_import_cites():
    files = {"model.py": ["import torch", "layer = torch.nn.Linear(3, 4)"]}
    assert _scan("PyTorch", files) == [("model.py", 1)]


def test_aliased_import_cites():
    assert _scan("Pandas", {"etl.py": ["import pandas as pd", "df = pd.DataFrame()"]}) == [("etl.py", 1)]


def test_lowercase_sql_in_sql_file_cites():
    files = {"schema.sql": ["-- create the users table", "create table users (id int);"]}
    assert _scan("SQL", files) == [("schema.sql", 2)]


def test_sql_comment_in_sql_file_does_not_set_match_line():
    files = {"q.sql": ["-- SELECT everything nightly", "select id from users;"]}
    assert _scan("SQL", files) == [("q.sql", 2)]


def test_shell_git_and_docker_commands_cite():
    assert _scan("Git", {"deploy.sh": ["#!/bin/sh", "git pull origin main"]}) == [("deploy.sh", 2)]
    files = {"build.sh": ["#!/bin/bash", "docker build -t app .", "docker run app"]}
    assert _scan("Docker", files) == [("build.sh", 2)]


def test_shebang_is_code_not_comment():
    assert _scan("Linux / Shell", {"run.sh": ["#!/bin/bash", "echo hi"]}) == [("run.sh", 1)]


def test_ci_keys_in_workflow_path_cite():
    files = {".github/workflows/ci.yml": ["name: ci", "jobs:", "  build:"]}
    assert _scan("CI/CD", files) == [(".github/workflows/ci.yml", 2)]


def test_command_position_python_docker_cites_via_shell_only():
    # A docker CLI signature in a non-shell file requires command position; inside a
    # Python string it stays hidden (abstain), while a shell script cites normally.
    files = {"task.py": ['subprocess.run("docker build .", shell=True)']}
    assert _scan("Docker", files) == []
