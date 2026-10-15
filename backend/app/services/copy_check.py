"""Earlier-source check: search GitHub for a distinctive cited line in other people's repos.

Runs after verification. A single matching line is advisory (shared tutorials,
boilerplate). A citation is only excluded as an **earlier source** when a second,
different line from it is in the same file elsewhere, and that file first appeared more
than a day before the candidate's file. Exclusion means "can't attribute to the
candidate", not "copied".
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.config import CACHE_DIR, GITHUB_TOKEN
from app.services.github_ingest import oldest_commit_date

API_ROOT = "https://api.github.com"
MAX_SEARCHES = 8  # GitHub code search allows ~10 requests/minute
MIN_LINE_LENGTH = 30
TOO_COMMON = 50
MAX_DATE_LOOKUPS = 5  # matching files whose first-appearance date is compared with the candidate's file
EARLIER_MARGIN = timedelta(days=1)  # same-day matches are not treated as earlier
LABEL = "Earlier public source"
BOILERPLATE = re.compile(
    r"^(import |from \S+ import |#include|package |using |export \* |require\(|//|#|/\*|\*)"
)


def _distinctive_lines(snippet: str, count: int) -> list[str]:
    """Up to `count` distinct searchable lines, longest first."""
    candidates = sorted(
        (
            line.strip() for line in snippet.splitlines()
            if len(line.strip()) >= MIN_LINE_LENGTH and not BOILERPLATE.match(line.strip())
        ),
        key=len,
        reverse=True,
    )
    lines: list[str] = []
    for candidate in candidates:
        line = candidate[:120].replace('"', " ")
        if line not in lines:
            lines.append(line)
        if len(lines) == count:
            break
    return lines


def _cache_path(query: str) -> Path:
    return CACHE_DIR / f"codesearch-{hashlib.sha256(query.encode()).hexdigest()[:24]}.json"


def _code_search(client: httpx.Client, query: str) -> dict[str, Any] | None:
    cache_path = _cache_path(query)
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    try:
        response = client.get(
            f"{API_ROOT}/search/code", params={"q": query, "per_page": 30}
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None  # rate limit or search error: report "not checked"
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _normalise(line: str) -> str:
    """The form `_distinctive_lines` searches for."""
    return line.strip()[:120].replace('"', " ")


def _file_contains(client: httpx.Client, full_name: str, path: str, line: str) -> bool | None:
    """Whether the other repository's file contains `line`; None when it couldn't be read."""
    try:
        response = client.get(
            f"{API_ROOT}/repos/{full_name}/contents/{quote(path, safe='/')}",
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        response.raise_for_status()
        text = response.text
    except (httpx.HTTPError, ValueError):
        return None
    return any(_normalise(other) == line for other in text.splitlines())


def _file_first_seen(client: httpx.Client, full_name: str, path: str) -> str | None:
    """When the matching file first appeared in the other repository (its oldest commit)."""
    owner, _, repo = full_name.partition("/")
    return oldest_commit_date(client, owner, repo, path)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _search(client: httpx.Client, line: str, username: str) -> dict[str, Any] | None:
    data = _code_search(client, f'"{line}"')
    if data is None:
        return None
    others = []
    for item in data.get("items", []):
        repository = item.get("repository") or {}
        owner = (repository.get("owner") or {}).get("login", "")
        if owner.lower() == username.lower() or repository.get("fork"):
            continue
        if any(other["repo"] == repository.get("full_name") for other in others):
            continue
        others.append({
            "repo": repository.get("full_name"), "url": item.get("html_url"), "path": item.get("path"),
        })
    total = data.get("total_count", 0) - (len(data.get("items", [])) - len(others))
    return {"total": max(total, len(others)), "matches": others}


def _spend(budget: dict[str, int] | None) -> bool:
    """Take one search from the per-passport budget; None means unlimited."""
    if budget is None:
        return True
    if budget["left"] <= 0:
        return False
    budget["left"] -= 1
    return True


def _not_checked_for_budget() -> dict[str, Any]:
    return {"id": "copy_match", "label": LABEL, "status": "unknown",
            "detail": f"Not checked (search limit of {MAX_SEARCHES} searches per passport)."}


def earlier_source_detail(source_repo: str, source_first_seen: str, candidate_first_seen: str) -> str:
    return (
        f"Matches code that appeared in {source_repo} on {source_first_seen[:10]}, before this file's "
        f"first commit here ({candidate_first_seen[:10]}). Not counted as the candidate's own work. "
        "This may be legitimate reuse; review if relevant."
    )


def copy_signal(
    evidence: dict[str, Any],
    client: httpx.Client | None,
    username: str,
    candidate_first_seen: str | None = None,
    budget: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compare a citation with matching code elsewhere, by when each *file* first appeared.

    Repo creation dates are not used: a repo created earlier may have added the code later
    (possibly from the candidate). Unknown dates never lead to an exclusion.
    """
    if client is None:
        return {"id": "copy_match", "label": LABEL, "status": "unknown",
                "detail": "Not checked (GitHub code search needs a token, and is limited per minute)."}
    lines = _distinctive_lines(evidence.get("snippet", ""), 2)
    if not lines:
        return {"id": "copy_match", "label": LABEL, "status": "unknown",
                "detail": "No distinctive line in the citation to search for."}
    if not _spend(budget):
        return _not_checked_for_budget()
    result = _search(client, lines[0], username)
    if result is None:
        return {"id": "copy_match", "label": LABEL, "status": "unknown",
                "detail": "GitHub code search was unavailable (rate limit or error)."}
    total = result["total"]
    if total == 0:
        return {"id": "copy_match", "label": LABEL, "status": "ok",
                "detail": "A distinctive cited line was not found in other public repositories."}
    if total > TOO_COMMON:
        return {"id": "copy_match", "label": LABEL, "status": "unknown",
                "detail": f"The searched line appears in {total} repositories; too common to judge."}
    plural = "y" if total == 1 else "ies"
    candidate = _parse_date(candidate_first_seen)
    looked_up = result["matches"][:MAX_DATE_LOOKUPS]
    earlier, undetermined = [], 0
    for match in looked_up:
        other_seen = (
            _file_first_seen(client, match["repo"], match["path"])
            if candidate and match.get("path") else None
        )
        other = _parse_date(other_seen)
        if other is None:
            undetermined += 1  # unknown date: never an exclusion
        elif other < candidate - EARLIER_MARGIN:
            earlier.append((other, other_seen, match))
    if earlier:
        # Two different lines from the citation must be in that same, earlier file.
        _, source_seen, source = min(earlier, key=lambda item: item[0])
        if len(lines) > 1 and _file_contains(client, source["repo"], source["path"], lines[1]):
            return {"id": "copy_match", "label": LABEL, "status": "warn",
                    "detail": earlier_source_detail(source["repo"], source_seen, candidate_first_seen),
                    "matches": [match for _, _, match in earlier][:3],
                    "earlier_source": True,
                    "source_repo": source["repo"],
                    "source_url": source.get("url") or f"https://github.com/{source['repo']}",
                    "source_first_seen": source_seen,
                    "candidate_first_seen": candidate_first_seen}
        return {"id": "copy_match", "label": LABEL, "status": "warn",
                "detail": f"A cited line also appears in {total} other repositor{plural}, including "
                          f"{len(earlier)} where the file appeared before this file's first commit here. "
                          "Only one line matched, so it still counts; review if relevant.",
                "matches": [match for _, _, match in earlier][:3]}
    if undetermined:
        return {"id": "copy_match", "label": LABEL, "status": "warn",
                "detail": f"A cited line also appears in {total} other repositor{plural}; it could not be "
                          "determined which file appeared first, so it still counts. Review if relevant.",
                "matches": result["matches"][:3]}
    if total <= len(looked_up):
        return {"id": "copy_match", "label": LABEL, "status": "ok",
                "detail": f"A cited line also appears in {total} other repositor{plural}, where the file "
                          "appeared after (or within a day of) this file's first commit here."}
    return {"id": "copy_match", "label": LABEL, "status": "unknown",
            "detail": f"A cited line also appears in {total} other repositor{plural}; "
                      "not all of them could be compared.",
            "matches": result["matches"][:3]}


def add_copy_signals(
    skills: list[dict[str, Any]], username: str, first_seen: dict[str, str] | None = None
) -> None:
    """`first_seen` maps "repo/path" to when that file first appeared in the candidate's repo."""
    # Attach a copy_match signal to verified code evidence, most important first, within the search budget.
    code_items = [
        item for skill in skills for item in skill.get("evidence", [])
        if item.get("type") == "code" and "integrity" in item
    ]
    if not code_items:
        return
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Skill-Passport-Prototype",
    }
    if not GITHUB_TOKEN:
        for item in code_items:
            item["integrity"].append(copy_signal(item, None, username))
        return
    headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    # Confirmation searches come out of the same budget as first searches.
    budget = {"left": MAX_SEARCHES}
    with httpx.Client(headers=headers, timeout=httpx.Timeout(20.0, connect=10.0)) as client:
        for item in code_items:
            item["integrity"].append(
                copy_signal(
                    item, client, username, (first_seen or {}).get(f"{item['repo']}/{item['file_path']}"), budget,
                )
            )
