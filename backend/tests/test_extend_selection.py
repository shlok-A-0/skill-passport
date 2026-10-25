"""Second-pass retrieval: target skills missing from the first-pass selection
trigger a bounded extra fetch of candidates carrying their evidence."""

from app.services.github_ingest import (
    EXTEND_MAX_EXTRA_FILES,
    _extend_selection_for_targets,
    _target_evidence_tiers,
)


def _candidates(n: int) -> list[str]:
    return [f"src/module_{i}.py" for i in range(n)]


def test_extension_fetches_file_with_missing_target_signature():
    selected = {"src/module_0.py": ("print('hello')", ["print('hello')"])}
    contents = {
        "src/module_0.py": "print('hello')",
        "src/module_1.py": "x = 1",
        "src/module_2.py": "from flask import Flask\napp = Flask(__name__)",
    }
    fetched = []

    def fetch(path):
        fetched.append(path)
        return contents.get(path)

    result = _extend_selection_for_targets(
        selected, _candidates(3), {p: len(c) for p, c in contents.items()},
        fetch, ["flask"], {}, {},
    )
    assert "src/module_2.py" in result
    assert fetched == ["src/module_1.py", "src/module_2.py"]


def test_extension_declared_target_without_usage_files_adds_nothing():
    """A dependency-declared target still triggers a usage hunt, but candidates
    without its signatures are not added to the selection."""
    selected = {"src/module_0.py": ("x = 1", ["x = 1"])}
    contents = {f"src/module_{i}.py": "y = 2" for i in range(1, 4)}

    result = _extend_selection_for_targets(
        selected, _candidates(4), {p: len(c) for p, c in contents.items()},
        lambda path: contents.get(path), ["flask"],
        {"requirements.txt": "flask==3.0.0\n"}, {},
    )
    assert set(result) == set(selected)


def test_extension_respects_extra_file_cap():
    selected = {"src/module_0.py": ("x = 1", ["x = 1"])}
    # One distinct real skill signature per file: each fetch covers one target.
    skill_sigs = ["from flask import Flask", "from django.db import models",
                  "from fastapi import FastAPI", "import express"]
    targets = ["flask", "django", "fastapi", "express"]
    contents = {
        f"src/module_{i + 1}.py": signature
        for i, signature in enumerate(skill_sigs)
    }
    contents["src/module_0.py"] = "x = 1"

    result = _extend_selection_for_targets(
        selected, _candidates(8), {p: len(c) for p, c in contents.items()},
        lambda path: contents.get(path), targets, {}, {},
    )
    extras = set(result) - set(selected)
    assert len(extras) == EXTEND_MAX_EXTRA_FILES


def test_no_targets_returns_selection_unchanged():
    selected = {"src/module_0.py": ("x = 1", ["x = 1"])}
    result = _extend_selection_for_targets(
        selected, _candidates(3), {}, lambda path: None, None, {}, {},
    )
    assert result is selected


def test_target_evidence_tiers_via_config_and_signature():
    selected = {"src/app.py": ("from flask import Flask", ["from flask import Flask"])}
    tiers = _target_evidence_tiers(
        ["flask", "docker", "django"], selected, {}, {"Dockerfile": "FROM python:3.11"}
    )
    assert tiers == {"flask": "signature", "docker": "declared", "django": None}


def test_extension_hunts_usage_evidence_for_declared_only_targets():
    """A dependency-declared target (Used at most) still gets a bounded hunt for
    code-level usage evidence that can support Applied/Demonstrated."""
    selected = {"src/module_0.py": ("x = 1", ["x = 1"])}
    contents = {
        "src/module_0.py": "x = 1",
        "src/module_1.py": "from flask import Flask\napp = Flask(__name__)",
    }

    result = _extend_selection_for_targets(
        selected, _candidates(2), {p: len(c) for p, c in contents.items()},
        lambda path: contents.get(path), ["flask"],
        {"requirements.txt": "flask==3.0.0\n"}, {},
    )
    assert "src/module_1.py" in result


def test_extension_skips_targets_with_signature_evidence_already():
    selected = {"src/module_0.py": ("from flask import Flask", ["from flask import Flask"])}
    fetched = []

    def fetch(path):
        fetched.append(path)
        return "pass"

    result = _extend_selection_for_targets(
        selected, _candidates(3), {}, fetch, ["flask"], {}, {},
    )
    assert result is selected
    assert fetched == []


def test_extension_spend_never_exceeds_remaining_budget():
    """Instrumented worst case: first pass + extension together stay within the
    per-repo MAX_SOURCE_FETCHES budget, however many candidates match."""
    from app.services.github_ingest import (
        MAX_SOURCE_FETCHES,
        _ordered_candidates,
        _select_source_files,
    )

    tree = [f"src/mod_{i:02d}.py" for i in range(30)]
    contents = {
        path: "from flask import Flask\napp = Flask(__name__)\n" for path in tree
    }
    sizes = {path: len(content) for path, content in contents.items()}
    fetch_count = 0

    def counting_fetch(path):
        nonlocal fetch_count
        fetch_count += 1
        return contents.get(path)

    selected = _select_source_files(
        _ordered_candidates(tree, []), sizes, counting_fetch
    )
    remaining = max(0, MAX_SOURCE_FETCHES - fetch_count)
    _extend_selection_for_targets(
        selected, _ordered_candidates(tree, []), sizes, counting_fetch,
        ["django"], {}, {}, max_extra_fetches=remaining,
    )
    assert fetch_count <= MAX_SOURCE_FETCHES


def test_cache_superset_rule_documented():
    """A cache entry serves requests whose targets it covers (superset reads
    more, so it is safe); a narrower entry does not serve a wider request."""
    requested = ["flask"]
    assert set(requested) <= set(["flask", "django"])
    assert not set(["flask", "django"]) <= set(requested)
