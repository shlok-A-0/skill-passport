"""How much of the candidate's work was analysed.

Missing evidence often means "not in the files we read", not "lacks the skill", so every
passport records what was read, what was skipped and why.
"""
from typing import Any

from app.services.claim_proposer import _files_for_prompt

SKIP_LABELS = {
    "too_large": "too large to read",
    "too_many_lines": "longer than the line limit",
    "unreadable": "could not be fetched",
    "empty": "empty",
    "file_limit": "not read (per-repository file limit reached)",
}


def build_coverage(ingestion: dict[str, Any]) -> dict[str, Any]:
    sent = {(repo, path) for repo, path, _ in _files_for_prompt(ingestion)}
    repos = []
    notes = []
    for repo in ingestion.get("repos", []):
        stats = repo.get("coverage") or {}
        read_paths = list(repo.get("source_files", {}))
        dropped = sum(1 for path in read_paths if (repo["name"], path) not in sent)
        repos.append({
            "name": repo["name"],
            "source_files_total": stats.get("source_files_total", repo.get("source_file_count", len(read_paths))),
            "source_files_read": stats.get("source_files_read", len(read_paths)),
            "lines_read": stats.get(
                "lines_read", sum(source.get("line_count", 0) for source in repo.get("source_files", {}).values())
            ),
            "skipped": stats.get("skipped", {}),
            "files_dropped_by_line_cap": dropped,
        })
        if stats.get("tree_truncated") or repo.get("tree_truncated"):
            notes.append(f"Tree truncated for repo {repo['name']}: GitHub returned only part of the file list.")
        if dropped:
            notes.append(f"{dropped} file(s) from {repo['name']} were read but left out of the AI proposal step (line limit).")
        supporting_skipped = repo.get("supporting_files_not_read", 0)
        if supporting_skipped:
            notes.append(
                f"{supporting_skipped} dependency or configuration file(s) from {repo['name']} were not read (supporting-file budget)."
            )
    return {
        "repos_listed": ingestion.get("repos_listed", len(repos)),
        "repos_analysed": len(repos),
        "repos_skipped": ingestion.get("skipped_repos", []),
        "source_files_total": sum(item["source_files_total"] for item in repos),
        "source_files_read": sum(item["source_files_read"] for item in repos),
        "lines_read": sum(item["lines_read"] for item in repos),
        "files_dropped_by_line_cap": sum(item["files_dropped_by_line_cap"] for item in repos),
        "repos": repos,
        "notes": notes,
    }


def coverage_phrase(coverage: dict[str, Any] | None) -> str:
    """"(N of M source files read)" for reasons; empty when coverage is unknown."""
    if not coverage:
        return ""
    return f" ({coverage['source_files_read']} of {coverage['source_files_total']} source files read)"
