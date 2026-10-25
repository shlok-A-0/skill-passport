import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.fixtures import FIXTURES
from eval.harness import run_all, run_fixture, summarize


def test_every_fixture_runs_and_reports_scope() -> None:
    report = run_all(FIXTURES)
    assert report["scope"]["evaluated"]
    assert report["scope"]["excluded"]
    assert len(report["fixtures"]) == len(FIXTURES)
    for split in ("tune", "heldout", "all"):
        assert split in report["summary"]


def test_buried_evidence_fixture_first_pass_misses_extension_recovers() -> None:
    """The buried-evidence fixture still exercises the sampling cap: the first-pass
    selection alone does not reach the evidence file, and the target-driven second
    pass (wired in run_fixture) recovers it."""
    from app.services.github_ingest import _ordered_candidates, _select_source_files

    fixture = next(f for f in FIXTURES if f["id"] == "tune-buried-evidence")
    repo = fixture["ingestion"]["repos"][0]
    all_files = repo["all_files"]
    sizes = {path: len(content.encode("utf-8")) for path, content in all_files.items()}
    first_pass = _select_source_files(
        _ordered_candidates(repo["tree"], []), sizes, all_files.get
    )
    assert "src/omega_api.py" not in first_pass  # the sampling miss is real

    result = run_fixture(fixture)
    assert result["sampling"]["fetch_stage_simulated"] is True
    flask = next(c for c in result["comparisons"] if c["skill_id"] == "flask")
    assert flask["expected"] == "Applied" and flask["actual"] == "Applied"


def test_copied_blame_fixture_excludes_citation() -> None:
    fixture = next(f for f in FIXTURES if f["id"] == "tune-copied-blame")
    result = run_fixture(fixture)
    assert result["rejected_claims"] >= 1
    assert not result["absent_violations"]


def test_metrics_count_over_and_underrating() -> None:
    results = [
        {
            "split": "tune",
            "detection": {"tp": 1, "fp": 0, "fn": 1},
            "comparisons": [
                {"skill_id": "a", "expected": "Applied", "actual": "Applied", "exact": True, "distance": 0},
                {"skill_id": "b", "expected": "Used", "actual": "Demonstrated", "exact": False, "distance": 2},
                {"skill_id": "c", "expected": "Demonstrated", "actual": "Used", "exact": False, "distance": -2},
            ],
            "absent_violations": [],
            "sampling": {"claims_dropped_by_sampling": []},
        }
    ]
    summary = summarize(results)["tune"]
    assert summary["levels"]["overrated"] == 1
    assert summary["levels"]["underrated"] == 1
    assert summary["levels"]["exact"] == 1
    assert summary["detection"]["recall"] == 0.5


def test_heldout_fixtures_are_not_in_tune_summary() -> None:
    report = run_all(FIXTURES)
    tune_ids = {f["id"] for f in FIXTURES if f["split"] == "tune"}
    heldout_ids = {f["id"] for f in FIXTURES if f["split"] == "heldout"}
    assert tune_ids and heldout_ids and not (tune_ids & heldout_ids)
    assert report["summary"]["tune"]["fixtures"] == len(tune_ids)
    assert report["summary"]["heldout"]["fixtures"] == len(heldout_ids)


def test_challenge_upgrade_fixture_baseline_underrates_extension_recovers() -> None:
    """Dependency-declared flask with buried usage evidence: first-pass-only and
    declared-capped behavior leave it at Used; the bounded usage hunt recovers Applied."""
    import eval.harness as harness

    fixture = next(f for f in FIXTURES if f["id"] == "challenge-buried-usage-upgrade")
    original = harness._extend_selection_for_targets
    harness._extend_selection_for_targets = lambda selected, *a, **k: selected
    baseline = run_fixture(fixture)
    harness._extend_selection_for_targets = original

    base_flask = next(c for c in baseline["comparisons"] if c["skill_id"] == "flask")
    assert base_flask["expected"] == "Applied" and base_flask["actual"] == "Used"

    result = run_fixture(fixture)
    flask = next(c for c in result["comparisons"] if c["skill_id"] == "flask")
    assert flask["expected"] == "Applied" and flask["actual"] == "Applied"


def test_challenge_deep_burial_documents_persistent_miss() -> None:
    """Evidence ranked beyond the second-pass fetch budget is still missed; this
    residual bound is intentional and kept visible."""
    fixture = next(f for f in FIXTURES if f["id"] == "challenge-deep-burial")
    result = run_fixture(fixture)
    flask = next(c for c in result["comparisons"] if c["skill_id"] == "flask")
    assert flask["expected"] == "Applied" and flask["actual"] == "Claimed"
    assert result["sampling"]["claims_dropped_by_sampling"]


def test_challenge_buried_evidence_recovers_on_second_pass() -> None:
    fixture = next(f for f in FIXTURES if f["id"] == "challenge-buried-evidence")
    result = run_fixture(fixture)
    fastapi = next(c for c in result["comparisons"] if c["skill_id"] == "fastapi")
    assert fastapi["expected"] == "Applied" and fastapi["actual"] == "Applied"


def test_fixture_fetch_counts_stay_within_per_repo_budget() -> None:
    from app.services.github_ingest import MAX_SOURCE_FETCHES

    for fixture in FIXTURES:
        result = run_fixture(fixture)
        for repo, count in result["sampling"]["fetch_counts"].items():
            assert count <= MAX_SOURCE_FETCHES, (fixture["id"], repo, count)
