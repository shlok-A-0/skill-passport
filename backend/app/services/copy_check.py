"""Copy detection: search GitHub for a distinctive cited line in other people's repos.

Runs after verification and only adds an advisory integrity signal. A match is a
reason for a human to look, not proof of copying (shared tutorials, boilerplate).
"""
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx

from app.config import CACHE_DIR, GITHUB_TOKEN

API_ROOT = "https://api.github.com"
MAX_SEARCHES = 8  # GitHub code search allows ~10 requests/minute
MIN_LINE_LENGTH = 30
TOO_COMMON = 50
MAX_DATE_LOOKUPS = 5  # other repos whose creation date is compared with the student's repo
LABEL = "Code not found in other repositories"
BOILERPLATE = re.compile(
    r"^(import |from \S+ import |#include|package |using |export \* |require\(|//|#|/\*|\*)"
)


def _distinctive_line(snippet: str) -> str | None:
    candidates = [
        line.strip() for line in snippet.splitlines()
        if len(line.strip()) >= MIN_LINE_LENGTH and not BOILERPLATE.match(line.strip())
    ]
    if not candidates:
        return None
    line = max(candidates, key=len)[:120]
    return line.replace('"', " ")


def _cache_path(query: str) -> Path:
    return CACHE_DIR / f"codesearch-{hashlib.sha256(query.encode()).hexdigest()[:24]}.json"


def _search(client: httpx.Client, line: str, username: str) -> dict[str, Any] | None:
    query = f'"{line}"'
    cache_path = _cache_path(query)
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        try:
            response = client.get(
                f"{API_ROOT}/search/code", params={"q": query, "per_page": 30}
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return None  # rate limit or search error: report "not checked"
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    others = []
    for item in data.get("items", []):
        repository = item.get("repository") or {}
        owner = (repository.get("owner") or {}).get("login", "")
        if owner.lower() == username.lower() or repository.get("fork"):
            continue
        if any(other["repo"] == repository.get("full_name") for other in others):
            continue
        others.append({"repo": repository.get("full_name"), "url": item.get("html_url")})
    total = data.get("total_count", 0) - (len(data.get("items", [])) - len(others))
    return {"total": max(total, len(others)), "matches": others}


def _repo_created_at(client: httpx.Client, full_name: str) -> str | None:
    try:
        response = client.get(f"{API_ROOT}/repos/{full_name}")
        response.raise_for_status()
        return response.json().get("created_at")
    except (httpx.HTTPError, ValueError):
        return None


def copy_signal(
    evidence: dict[str, Any],
    client: httpx.Client | None,
    username: str,
    repo_created_at: str | None = None,
) -> dict[str, Any]:
    if client is None:
        return {"id": "copy_match", "label": LABEL, "status": "unknown",
                "detail": "Not checked (GitHub code search needs a token, and is limited per minute)."}
    line = _distinctive_line(evidence.get("snippet", ""))
    if not line:
        return {"id": "copy_match", "label": LABEL, "status": "unknown",
                "detail": "No distinctive line in the citation to search for."}
    result = _search(client, line, username)
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
    # Only a repo that existed before the student's repo can be the source of a copy;
    # later repos may have copied from the student.
    looked_up = result["matches"][:MAX_DATE_LOOKUPS]
    older, unknown = [], 0
    for match in looked_up:
        created = _repo_created_at(client, match["repo"]) if repo_created_at else None
        if created is None:
            unknown += 1
        elif created < repo_created_at:
            older.append(match)
    if older:
        return {"id": "copy_match", "label": LABEL, "status": "warn",
                "detail": f"A cited line also appears in {total} other repositor{plural}, "
                          f"including {len(older)} created before this one. Check whether it was copied.",
                "matches": older[:3]}
    if not unknown and total <= len(looked_up):
        return {"id": "copy_match", "label": LABEL, "status": "ok",
                "detail": f"A cited line also appears in {total} other repositor{plural}, all created "
                          "after this one (they may have copied from it)."}
    return {"id": "copy_match", "label": LABEL, "status": "unknown",
            "detail": f"A cited line also appears in {total} other repositor{plural}; "
                      "could not confirm which came first.",
            "matches": result["matches"][:3]}


def add_copy_signals(
    skills: list[dict[str, Any]], username: str, repo_created: dict[str, str] | None = None
) -> None:
    """Attach a copy_match signal to verified code evidence, most important first, within the search budget."""
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
    with httpx.Client(headers=headers, timeout=httpx.Timeout(20.0, connect=10.0)) as client:
        for index, item in enumerate(code_items):
            if index < MAX_SEARCHES:
                item["integrity"].append(
                    copy_signal(item, client, username, (repo_created or {}).get(item["repo"]))
                )
            else:
                item["integrity"].append(
                    {"id": "copy_match", "label": LABEL, "status": "unknown",
                     "detail": f"Not checked (search limit of {MAX_SEARCHES} citations per passport)."}
                )
