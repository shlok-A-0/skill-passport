"""Offline evaluation runner.

Evaluated components (named explicitly):
1. File sampling/selection: which fixture files reach the AI prompt budget
   (app.services.claim_proposer._files_for_prompt).
2. Deterministic stages given specified claims: app.services.verifier
   (verify_claims, exclude_uncounted_citations, compute_level).

Replaced or excluded components (never evaluated here):
- Gemini claim proposal: replaced by a stand-in that emits the fixture's
  available_claims for sampled files only. This models a perfect reader of
  the sampled files; real LLM recall is worse or different. No paid API calls.
- copy_check earlier-source search: needs live GitHub code search (network);
  not exercised. Blame-based exclusion (not_written_by_candidate) runs offline
  and IS evaluated (tune-copied-blame).
- End-to-end live baseline (GitHub ingest + Gemini): outstanding; requires the
  user's laptop workflow.

Metrics follow the design agreed with the user: detection precision/recall/F1,
level exact/within-one/over/under-rating, and citation errors, per axis with
sample sizes. No single accuracy percentage is produced.
"""
from __future__ import annotations

from typing import Any

from app.services.claim_proposer import _files_for_prompt
from app.services.github_ingest import (
    EXTEND_MAX_EXTRA_FETCHES,
    MAX_SOURCE_FETCHES,
    _extend_selection_for_targets,
    _ordered_candidates,
    _select_source_files,
)
from app.services.verifier import LEVEL_ORDER, exclude_uncounted_citations, verify_claims

LEVELS = ["Claimed", "Used", "Applied", "Demonstrated"]


def _positive(level: str | None) -> bool:
    return bool(level) and LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER["Used"]


def _apply_fetch_selection(
    repo: dict[str, Any], target_skills: list[str] | None = None
) -> dict[str, Any]:
    """Run the real ingest file selection over a fixture repo's full tree.

    Repos with an "all_files" map (path -> content) simulate the fetch stage:
    the production _ordered_candidates/_select_source_files choose which files
    are read, so the per-repo MAX_SOURCE_FILES cap genuinely applies. Repos
    with a prebuilt "source_files" map skip this stage (documented per fixture).
    """
    all_files = repo.get("all_files")
    if not all_files:
        return repo
    tree = repo["tree"]
    sizes = {path: len(content.encode("utf-8")) for path, content in all_files.items()}
    fetched: dict[str, tuple[str, list[str]]] = {}

    def fetch(path: str):
        content = all_files.get(path)
        if content is None:
            return None
        return content

    fetch_count = 0

    def counting_fetch(path: str):
        nonlocal fetch_count
        fetch_count += 1
        return fetch(path)

    candidates = _ordered_candidates(tree, [])
    chosen = _select_source_files(candidates, sizes, counting_fetch)
    # Mirror production: the resume/role target skills steer a bounded extra fetch
    # that spends only the per-repo budget the first pass left unused.
    remaining = max(0, min(EXTEND_MAX_EXTRA_FETCHES, MAX_SOURCE_FETCHES - fetch_count))
    chosen = _extend_selection_for_targets(
        chosen,
        candidates,
        sizes,
        counting_fetch,
        target_skills,
        repo.get("dependency_contents", {}),
        repo.get("config_contents", {}),
        max_extra_fetches=remaining,
    )
    source_files = {
        path: {
            "content": content,
            "lines": lines,
            "line_count": len(lines),
            "authored_shas": ["c1"],
        }
        for path, (content, lines) in chosen.items()
    }
    repo = {key: value for key, value in repo.items() if key != "all_files"}
    return {
        **repo,
        "source_files": source_files,
        "selection_total": len(all_files),
        "fetch_count": fetch_count,
    }


def run_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    ingestion = dict(fixture["ingestion"])
    selection_totals = 0
    has_selection = False
    repos = []
    for repo in ingestion["repos"]:
        has_selection = has_selection or bool(repo.get("all_files"))
        selection_totals += len(repo.get("all_files") or repo.get("source_files") or {})
        target_skills = [
            mention["skill_id"]
            for mention in fixture.get("resume_skills", [])
            if mention.get("skill_id")
        ]
        repos.append(_apply_fetch_selection(repo, target_skills))
    ingestion = {**ingestion, "repos": repos}
    prompt_files = {
        (repo, path) for repo, path, _ in _files_for_prompt(ingestion)
    }
    total_files = selection_totals

    # Stand-in proposer: a claim exists only if its file reached the prompt.
    claims = [
        claim
        for claim in fixture.get("available_claims", [])
        if (claim["repo"], claim["file_path"]) in prompt_files
    ]
    dropped_claims = [
        claim
        for claim in fixture.get("available_claims", [])
        if (claim["repo"], claim["file_path"]) not in prompt_files
    ]

    verified = verify_claims(
        claims,
        ingestion,
        resume_skills=fixture.get("resume_skills") or [],
        certificate_skills=fixture.get("certificate_skills") or [],
    )
    exclude_uncounted_citations(verified, ingestion["repos"])

    got = {skill["skill_id"]: skill["level"] for skill in verified["skills"]}
    labels = fixture["labels"]
    expected_levels: dict[str, str] = labels.get("levels", {})
    absent: list[str] = labels.get("absent", [])

    comparisons = []
    for skill_id, expected in sorted(expected_levels.items()):
        actual = got.get(skill_id, "Claimed")
        distance = LEVEL_ORDER[actual] - LEVEL_ORDER[expected]
        comparisons.append({
            "skill_id": skill_id,
            "expected": expected,
            "actual": actual,
            "exact": actual == expected,
            "distance": distance,
            "direction": (
                "match" if distance == 0 else ("overrated" if distance > 0 else "underrated")
            ),
        })
    absent_violations = [
        {"skill_id": skill_id, "actual": got[skill_id]}
        for skill_id in absent
        if _positive(got.get(skill_id))
    ]

    # Detection metrics over the labeled universe: expected-positive skills plus absent skills.
    expected_positive = {
        skill_id for skill_id, level in expected_levels.items() if _positive(level)
    }
    detected_positive = {
        skill_id
        for skill_id in expected_positive | set(absent)
        if _positive(got.get(skill_id))
    }
    tp = len(expected_positive & detected_positive)
    fp = len(detected_positive - expected_positive)
    fn = len(expected_positive - detected_positive)

    return {
        "fixture_id": fixture["id"],
        "split": fixture["split"],
        "labeled_by": fixture.get("labeled_by"),
        "sampling": {
            "files_in_prompt": len({path for _, path in prompt_files}),
            "source_files_total": total_files,
            "fetch_stage_simulated": has_selection,
            "fetch_counts": {
                repo["name"]: repo.get("fetch_count")
                for repo in ingestion["repos"]
                if repo.get("fetch_count") is not None
            },
            "claims_proposed": len(claims),
            "claims_dropped_by_sampling": [
                {"skill_id": claim["skill_id"], "file_path": claim["file_path"]}
                for claim in dropped_claims
            ],
        },
        "comparisons": comparisons,
        "absent_violations": absent_violations,
        "detection": {"tp": tp, "fp": fp, "fn": fn},
        "rejected_claims": len(verified["rejected_claims"]),
    }


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall, "f1": f1,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def aggregate(group: list[dict[str, Any]]) -> dict[str, Any]:
        tp = sum(item["detection"]["tp"] for item in group)
        fp = sum(item["detection"]["fp"] for item in group)
        fn = sum(item["detection"]["fn"] for item in group)
        comparisons = [c for item in group for c in item["comparisons"]]
        exact = sum(1 for c in comparisons if c["exact"])
        within_one = sum(1 for c in comparisons if abs(c["distance"]) <= 1)
        over = sum(1 for c in comparisons if c["distance"] > 0)
        under = sum(1 for c in comparisons if c["distance"] < 0)
        return {
            "fixtures": len(group),
            "labeled_skills": len(comparisons),
            "detection": _prf(tp, fp, fn),
            "levels": {
                "exact": exact,
                "within_one": within_one,
                "overrated": over,
                "underrated": under,
                "total": len(comparisons),
                "exact_rate": exact / len(comparisons) if comparisons else None,
                "within_one_rate": within_one / len(comparisons) if comparisons else None,
            },
            "absent_violations": sum(len(item["absent_violations"]) for item in group),
            "claims_dropped_by_sampling": sum(
                len(item["sampling"]["claims_dropped_by_sampling"]) for item in group
            ),
        }

    return {
        "tune": aggregate([r for r in results if r["split"] == "tune"]),
        "heldout": aggregate([r for r in results if r["split"] == "heldout"]),
        # Post-hoc adversarial cases built after failures were seen; reported
        # separately and never merged into the held-out split.
        "challenge": aggregate([r for r in results if r["split"] == "challenge"]),
        "all": aggregate(results),
    }


def run_all(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    results = [run_fixture(fixture) for fixture in fixtures]
    return {
        "scope": {
            "evaluated": [
                "file sampling/selection (claim_proposer._files_for_prompt)",
                "deterministic verification, evidence and level assignment (verifier.py)",
            ],
            "excluded": [
                "Gemini claim proposal (stand-in: fixture available_claims on sampled files; no paid calls)",
                "copy_check earlier-source search (needs live GitHub code search)",
                "live end-to-end baseline (GitHub ingest + Gemini): outstanding for laptop workflow",
            ],
            "labels": (
                "Agent-authored from app/data/skills.json and verifier rules; "
                "no independent human review; not real-person proficiency ground truth."
            ),
        },
        "fixtures": results,
        "summary": summarize(results),
    }
