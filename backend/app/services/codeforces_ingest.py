"""Optional Codeforces evidence via the official public API.

This is a separate competitive-programming axis: it never feeds skill levels,
role matching or claim verification. A typed handle proves nothing about
ownership, so every payload carries "ownership": "unverified".

API facts (grounded against https://codeforces.com/apiHelp/ on 2026-10-25):
- Base URL https://codeforces.com/api/{methodName}, JSON, no auth for public data.
- user.info?handles=X -> User (rating, maxRating, rank, maxRank, contribution).
- user.rating?handle=X -> [RatingChange] (rated contest history).
- user.status?handle=X&from=1&count=N -> [Submission], newest first; source code
  (includeSources) is only available for the requester's own account, which the
  public integration is not, so only submission metadata is used.
- A failed call returns {"status": "FAILED", "comment": ...}; an unknown handle
  fails with "handles: User with handle ... not found".
- Official rate limit: at most 1 request per 2 seconds, enforced process-wide
  by _pace(); a "Call limit exceeded" answer gets one backoff retry.
- Any endpoint failure fails the whole fetch: partial data is never presented
  as complete (the caller retains prior data marked stale instead).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import re
import threading
import time
from typing import Any

import httpx

from app.config import CACHE_DIR
from app.services.json_cache import write_json_cache

CODEFORCES_API = "https://codeforces.com/api"
CACHE_TTL_SECONDS = 6 * 60 * 60
CACHE_SCHEMA_VERSION = 1
SUBMISSION_SAMPLE = 200  # recent submissions the aggregate stats are computed over
REQUEST_TIMEOUT = 15.0
# Official limit (https://codeforces.com/apiHelp/): "API may be requested at most
# 1 time per two seconds" - over the limit the API answers FAILED with "Call limit
# exceeded". The pacer is process-wide so concurrent jobs cannot exceed it together.
MIN_REQUEST_INTERVAL = 2.1
_LIMIT_RETRY_DELAY = 3.0
_RATE_LOCK = threading.Lock()
_last_request_at = 0.0
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,24}$")


def _pace() -> None:
    """Serialize API requests process-wide, at least MIN_REQUEST_INTERVAL apart."""
    global _last_request_at
    with _RATE_LOCK:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


class CodeforcesIngestError(RuntimeError):
    pass


class CodeforcesUserNotFoundError(CodeforcesIngestError):
    """The handle does not exist (or is not visible) on Codeforces."""


def validate_codeforces_handle(handle: str) -> str:
    handle = (handle or "").strip()
    if not _HANDLE_RE.match(handle):
        raise ValueError(
            "Enter a valid Codeforces handle (1-24 letters, digits, '_', '-' or '.')."
        )
    return handle


def _cache_path(handle: str):
    directory = CACHE_DIR / "codeforces"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{handle.lower()}.json"


def _call(client: httpx.Client, method: str, params: dict[str, Any], handle: str) -> Any:
    payload: dict[str, Any] | None = None
    for attempt in range(2):  # one retry, only for the API's own rate-limit answer
        _pace()
        try:
            response = client.get(f"{CODEFORCES_API}/{method}", params=params)
        except httpx.HTTPError as exc:
            raise CodeforcesIngestError(
                f"Codeforces is unreachable: {exc.__class__.__name__}"
            ) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise CodeforcesIngestError(f"Codeforces answered HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise CodeforcesIngestError("Codeforces returned a non-JSON response.") from exc
        comment = str(payload.get("comment", ""))
        if payload.get("status") == "OK":
            return payload.get("result")
        if "call limit exceeded" in comment.lower() and attempt == 0:
            time.sleep(_LIMIT_RETRY_DELAY)
            continue
        if "not found" in comment.lower():
            raise CodeforcesUserNotFoundError(f"No Codeforces user '{handle}'.")
        raise CodeforcesIngestError(f"Codeforces refused the request: {comment[:120]}")
    return payload.get("result") if payload else None


def _problem_key(submission: dict[str, Any]) -> str:
    problem = submission.get("problem") or {}
    contest = problem.get("contestId", submission.get("contestId", "?"))
    return f"{contest}:{problem.get('index', '?')}"


def _iso(epoch: Any) -> str | None:
    if not isinstance(epoch, (int, float)):
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _aggregate(submissions: list[dict[str, Any]]) -> dict[str, Any]:
    solved: dict[str, dict[str, Any]] = {}
    languages: Counter[str] = Counter()
    for submission in submissions:
        language = submission.get("programmingLanguage")
        if language:
            languages[language] += 1
        if submission.get("verdict") != "OK":
            continue
        key = _problem_key(submission)
        problem = submission.get("problem") or {}
        if key not in solved:
            solved[key] = {
                "rating": problem.get("rating"),
                "tags": problem.get("tags") or [],
            }
    tags: Counter[str] = Counter()
    for entry in solved.values():
        for tag in entry["tags"]:
            tags[tag] += 1
    solved_ratings = [entry["rating"] for entry in solved.values() if isinstance(entry["rating"], int)]
    return {
        "solved_count": len(solved),
        "solved_max_problem_rating": max(solved_ratings) if solved_ratings else None,
        "top_tags": [[tag, count] for tag, count in tags.most_common(10)],
        "languages": [[language, count] for language, count in languages.most_common(5)],
    }


def fetch_codeforces(handle: str, force_refresh: bool = False) -> dict[str, Any]:
    """Read a handle's public Codeforces data. Cached for CACHE_TTL_SECONDS;
    a cache hit serves only the same handle. Stats cover the most recent
    SUBMISSION_SAMPLE submissions, not the full history."""
    handle = validate_codeforces_handle(handle)
    cache_path = _cache_path(handle)
    if not force_refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            fresh = time.time() - cached.get("cached_at", 0) < CACHE_TTL_SECONDS
            if (
                fresh
                and cached.get("schema_version") == CACHE_SCHEMA_VERSION
                and isinstance(cached.get("data"), dict)
            ):
                return cached["data"]
        except (OSError, ValueError):
            cache_path.unlink(missing_ok=True)

    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        users = _call(client, "user.info", {"handles": handle}, handle)
        rating_history = _call(client, "user.rating", {"handle": handle}, handle)
        submissions = _call(
            client,
            "user.status",
            {"handle": handle, "from": 1, "count": SUBMISSION_SAMPLE},
            handle,
        )
    user = (users or [{}])[0]
    aggregates = _aggregate(submissions or [])
    result = {
        "handle": user.get("handle", handle),
        # A typed handle proves nothing; only a Codeforces-linked login flow
        # could verify ownership, and the public API has no such flow.
        "ownership": "unverified",
        "data_source": "codeforces_public_api",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "rating": user.get("rating"),
            "max_rating": user.get("maxRating"),
            "rank": user.get("rank"),
            "max_rank": user.get("maxRank"),
            "contribution": user.get("contribution"),
            "registered_at": _iso(user.get("registrationTimeSeconds")),
        },
        "rated_contests": len(rating_history or []),
        "last_rated_contest_at": _iso(
            max(
                (entry.get("ratingUpdateTimeSeconds", 0) for entry in (rating_history or [])),
                default=None,
            )
        ),
        # Requested window vs reality: stats cover the submissions actually
        # returned, which is fewer than SUBMISSION_SAMPLE for sparse profiles.
        "submission_sample": SUBMISSION_SAMPLE,
        "submissions_examined": len(submissions or []),
        **aggregates,
    }
    write_json_cache(
        cache_path,
        {"schema_version": CACHE_SCHEMA_VERSION, "cached_at": time.time(), "data": result},
    )
    return result
