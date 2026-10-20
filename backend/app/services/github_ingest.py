import base64
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.config import CACHE_DIR, GITHUB_TOKEN
from app.services.json_cache import write_json_cache

API_ROOT = "https://api.github.com"
DEPENDENCY_FILES = {
    "requirements.txt",
    "package.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "pyproject.toml",
}
SKIP_PARTS = {
    "node_modules",
    "dist",
    "build",
    "vendor",
    "migrations",
    ".git",
    "target",
    "coverage",
}
SOURCE_EXTENSIONS = {
    ".py", ".java", ".js", ".jsx", ".ts", ".tsx", ".go", ".cpp", ".cc",
    ".c", ".h", ".hpp", ".sql", ".sh",
}
IGNORED_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"}
MAX_FILE_BYTES = 256_000
MAX_LINES = 600
MAX_SOURCE_FILES = 6
# A source file this big can't fit in MAX_LINES; skip it without fetching (test files are truncated instead).
MAX_SOURCE_BYTES = 80_000
MIN_PREFERRED_LINES = 15  # shorter files are only used when nothing longer is left
MAX_SOURCE_FETCHES = 15
RECENT_COMMITS = 10  # the user's latest commits whose changed files are read first
CACHE_TTL_SECONDS = 6 * 60 * 60
DOCKER_FILES = {
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml",
}
CI_DIRS = (".github/workflows/", ".forgejo/workflows/", ".gitea/workflows/")
CI_FILES = {
    ".travis.yml", ".gitlab-ci.yml", ".circleci/config.yml", "Jenkinsfile",
    "azure-pipelines.yml", "bitbucket-pipelines.yml",
}
HISTORY_PAGE_SIZE = 100
# Cache files written before the current schema lack newer fields, so they are refetched.
CACHE_SCHEMA_VERSION = 9
MAX_REPOS = 5  # the most recently pushed non-fork public repos are analysed


class GitHubIngestError(RuntimeError):
    pass


class GitHubRateLimitError(GitHubIngestError):
    """Rate limits stop the job; other per-repo errors (404, 409 empty repo, 451) skip data."""


class GitHubUserNotFoundError(GitHubIngestError):
    """The requested account does not exist or is not visible to GitHub."""


class GitHubAuthenticationError(GitHubIngestError):
    """GitHub rejected the configured API credentials."""


def validate_github_username(username: str) -> str:
    normalized = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}", normalized):
        raise ValueError("Enter a GitHub username using 1–39 letters and numbers, with single hyphens between characters.")
    return normalized


def _cache_path(username: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", username.lower())
    return CACHE_DIR / f"github-{safe_name}.json"


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Skill-Passport-Prototype",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def _get(client: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
    response = client.get(url, **kwargs)
    if response.status_code == 404:
        if re.fullmatch(r"https://api\.github\.com/users/[^/]+", url):
            raise GitHubUserNotFoundError("GitHub account was not found.")
        raise GitHubIngestError(f"GitHub resource not found: {url}")
    if response.status_code == 401:
        raise GitHubAuthenticationError("GitHub rejected the configured API credentials.")
    message = response.reason_phrase
    if response.is_error:
        try:
            message = response.json().get("message", response.reason_phrase)
        except (ValueError, AttributeError):
            pass
    remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
    if response.status_code == 429 or (
        response.status_code == 403 and (remaining == "0" or "rate limit" in str(message).lower())
    ):
        reset = response.headers.get("X-RateLimit-Reset")
        detail = "GitHub API rate limit exceeded"
        if reset:
            detail += f"; reset at Unix time {reset}"
        raise GitHubRateLimitError(f"{detail} (remaining={remaining})")
    if response.is_error:
        raise GitHubIngestError(f"GitHub API returned {response.status_code}: {message}")
    return response


def _request(client: httpx.Client, url: str, **kwargs: Any) -> Any:
    return _get(client, url, **kwargs).json()


def _source_candidate(path: str) -> bool:
    parts = path.split("/")
    lower_parts = {part.lower() for part in parts}
    name = parts[-1]
    if lower_parts & SKIP_PARTS or name.lower() in IGNORED_NAMES:
        return False
    if name.endswith(".min.js") or name.endswith(".min.css"):
        return False
    return Path(name).suffix.lower() in SOURCE_EXTENSIONS


TEST_DIRS = {"tests", "test", "__tests__", "spec"}


def _named_like_test(path: str) -> bool:
    name = path.lower().split("/")[-1]
    return (
        name.startswith("test_")
        or Path(name).stem.endswith(("_test", "_spec"))
        or ".test." in name
        or ".spec." in name
    )


def is_test_path(path: str) -> bool:
    """A source file that is a test by its name or folder (docs like test_client.rst are not)."""
    parts = path.lower().split("/")
    name = parts[-1]
    if Path(name).suffix not in SOURCE_EXTENSIONS:
        return False
    return any(part in TEST_DIRS for part in parts[:-1]) or _named_like_test(path)


def _candidate_priority(path: str) -> tuple[int, int, str]:
    parts = path.lower().split("/")
    preferred = 0 if any(part in {"src", "app", "lib"} for part in parts) else 1
    return preferred, len(parts), path.lower()


def _recently_changed_paths(
    client: httpx.Client, owner: str, repo: str, user_commits: list[dict[str, Any]]
) -> list[str]:
    """Files changed in the user's latest commits, most recent first (new growth-task work shows up here)."""
    paths: list[str] = []
    for commit in user_commits[:RECENT_COMMITS]:
        sha = commit.get("sha")
        if not sha:
            continue
        try:
            detail = _request(client, f"{API_ROOT}/repos/{quote(owner)}/{quote(repo)}/commits/{sha}")
        except GitHubRateLimitError:
            raise
        except GitHubIngestError:
            continue
        for item in detail.get("files") or []:
            path = item.get("filename")
            if path and path not in paths:
                paths.append(path)
    return paths


def _ordered_candidates(tree: list[str], recent: list[str]) -> list[str]:
    """Test files first (one slot is reserved for a test), then other source files.

    Within each group, files the user changed recently come first, then `_candidate_priority`.
    """
    rank = {path: index for index, path in enumerate(recent)}

    def recency(path: str) -> tuple[bool, int]:
        return path not in rank, rank.get(path, 0)

    candidates = [path for path in tree if _source_candidate(path)]
    tests = sorted(
        (path for path in candidates if is_test_path(path) and Path(path).name != "__init__.py"),
        key=lambda path: (
            recency(path),
            not _named_like_test(path),
            not set(path.lower().split("/")[:-1]) & TEST_DIRS,
            _candidate_priority(path),
        ),
    )[:10]
    others = sorted(
        (path for path in candidates if not is_test_path(path)),
        key=lambda path: (recency(path), _candidate_priority(path)),
    )
    return tests + others


def _select_source_files(
    candidates: list[str], sizes: dict[str, int], fetch: Any,
    skipped: dict[str, int] | None = None,
) -> dict[str, tuple[str, list[str]]]:
    """Pick up to MAX_SOURCE_FILES readable files, preferring ones with MIN_PREFERRED_LINES or more.

    `skipped` (if given) counts files passed over for a reason other than the file limit:
    "too_large" (not fetched), "too_many_lines", "unreadable" or "empty".
    """
    chosen: dict[str, tuple[str, list[str]]] = {}
    short: dict[str, tuple[str, list[str]]] = {}
    fetches = 0
    reasons = skipped if skipped is not None else {}

    def has_test(group: dict[str, Any]) -> bool:
        return any(is_test_path(path) for path in group)

    def skip(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    for path in candidates:
        if len(chosen) >= MAX_SOURCE_FILES or fetches >= MAX_SOURCE_FETCHES:
            break
        test = is_test_path(path)
        if test and (has_test(chosen) or has_test(short)):
            continue
        limit = MAX_FILE_BYTES if test else MAX_SOURCE_BYTES
        if sizes.get(path, MAX_FILE_BYTES + 1) > limit:
            skip("too_large")
            continue
        fetches += 1
        content = fetch(path)
        if content is None:
            skip("unreadable")
            continue
        lines = content.splitlines()
        if lines and len(lines) > MAX_LINES and test:
            # Long test files: keep the first lines. Line numbers stay identical
            # to GitHub's, so citations inside them remain exact.
            lines = lines[:MAX_LINES]
            content = "\n".join(lines)
        if not lines:
            skip("empty")
            continue
        if len(lines) > MAX_LINES:
            skip("too_many_lines")
            continue
        (chosen if len(lines) >= MIN_PREFERRED_LINES else short)[path] = (content, lines)
    for path, value in short.items():
        if len(chosen) >= MAX_SOURCE_FILES:
            break
        chosen[path] = value
    return chosen


MAX_TEST_CONTENT_FETCHES = 5  # own budget: never uses the source-file budget
TEST_CONTENT_LINES = 300  # imports are near the top; keep cache files small
GENERIC_STEMS = {"__init__", "index", "main", "utils", "app"}


def module_stem(path: str) -> str | None:
    """A cited file's module name for test matching, or None when it's too short or generic."""
    stem = Path(path).stem.lower()
    for suffix in (".test", ".spec"):
        stem = stem.removesuffix(suffix)
    if len(stem) < 3 or stem in GENERIC_STEMS:
        return None
    return stem


def _test_files_to_read(test_files: list[str], source_paths: list[str]) -> list[str]:
    """Test files whose name shares a stem with a source file we read come first."""
    stems = {stem for stem in (module_stem(path) for path in source_paths if not is_test_path(path)) if stem}

    def shares_stem(path: str) -> bool:
        name = Path(path).name.lower()
        return any(re.search(rf"(?<![a-z0-9]){re.escape(stem)}", name) for stem in stems)

    return sorted(test_files, key=lambda path: (not shares_stem(path), _candidate_priority(path)))


def _test_contents(
    test_files: list[str], source_files: dict[str, dict[str, Any]], sizes: dict[str, int], fetch: Any
) -> dict[str, str]:
    """The top of up to MAX_TEST_CONTENT_FETCHES test files, for import matching ("Demonstrated")."""
    contents: dict[str, str] = {}
    fetches = 0
    for path in _test_files_to_read(test_files, list(source_files)):
        if path in source_files:  # already read as a source file: no fetch needed
            contents[path] = "\n".join(source_files[path]["lines"][:TEST_CONTENT_LINES])
            continue
        if fetches >= MAX_TEST_CONTENT_FETCHES:
            continue
        if sizes.get(path, MAX_FILE_BYTES + 1) > MAX_FILE_BYTES:
            continue
        fetches += 1
        content = fetch(path)
        if content:
            contents[path] = "\n".join(content.splitlines()[:TEST_CONTENT_LINES])
    return contents


def _test_authorship(
    client: httpx.Client, owner: str, repo: str, test_paths: list[str],
    source_files: dict[str, dict[str, Any]], username: str,
) -> dict[str, bool]:
    """Whether the user committed to each test file whose content was read ("Demonstrated" needs it).

    One lookup per test file, so the 5-fetch test budget bounds it. A file whose history can't be
    read is left out, and a skill it targets is not upgraded.
    """
    authored: dict[str, bool] = {}
    for path in test_paths:
        if path in source_files:  # already checked when it was read as a source file
            authored[path] = bool(source_files[path].get("authored_shas"))
            continue
        try:
            authored[path] = bool(_file_authorship(client, owner, repo, path, username))
        except GitHubRateLimitError:
            raise
        except GitHubIngestError:
            pass
    return authored


def _repo_error_reason(exc: Exception) -> str:
    message = str(exc)
    if "409" in message:
        return "empty repository"
    if "not found" in message.lower() or "404" in message:
        return "not found (404)"
    if "451" in message:
        return "unavailable for legal reasons (451)"
    return "could not be read"


def _repo_coverage(
    tree: list[str], source_files: dict[str, dict[str, Any]], skip_reasons: dict[str, int],
    tree_response: dict[str, Any],
) -> dict[str, Any]:
    """How much of this repo's source was actually read, and why the rest wasn't."""
    total = sum(1 for path in tree if _source_candidate(path))
    read = len(source_files)
    skipped = {reason: count for reason, count in skip_reasons.items() if count}
    # Everything else was never attempted: the per-repo file or fetch limit was reached first.
    not_attempted = total - read - sum(skipped.values())
    if not_attempted > 0:
        skipped["file_limit"] = not_attempted
    return {
        "source_files_total": total,
        "source_files_read": read,
        "lines_read": sum(source["line_count"] for source in source_files.values()),
        "skipped": skipped,
        "tree_truncated": bool(tree_response.get("truncated", False)),
    }


def _authored_paths(
    client: httpx.Client, owner: str, repo: str, paths: list[str], username: str
) -> dict[str, bool]:
    """Whether the user committed to each dependency/config file; it only counts towards "Used" if so.

    Test files aren't checked, and a file whose history can't be read is left out (= not authored).
    """
    authored: dict[str, bool] = {}
    for path in paths:
        if path in authored or is_test_path(path):
            continue
        try:
            authored[path] = bool(_file_authorship(client, owner, repo, path, username))
        except GitHubRateLimitError:
            raise
        except GitHubIngestError:
            pass
    return authored


def _get_file_content(client: httpx.Client, owner: str, repo: str, path: str, branch: str) -> str:
    data = _request(
        client,
        f"{API_ROOT}/repos/{quote(owner)}/{quote(repo)}/contents/{quote(path, safe='/')}",
        params={"ref": branch},
    )
    if isinstance(data, list):
        return ""
    if data.get("encoding") == "base64" and data.get("content"):
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    download_url = data.get("download_url")
    if download_url:
        response = _get(client, download_url)
        return response.text
    return ""


def _optional_content(
    client: httpx.Client, owner: str, repo: str, path: str, branch: str
) -> str | None:
    """File text, or None when this one file can't be read (rate limits still stop the job)."""
    try:
        return _get_file_content(client, owner, repo, path, branch)
    except GitHubRateLimitError:
        raise
    except (GitHubIngestError, httpx.HTTPError):
        return None


def _file_authorship(
    client: httpx.Client, owner: str, repo: str, path: str, username: str
) -> list[str]:
    commits = _request(
        client,
        f"{API_ROOT}/repos/{quote(owner)}/{quote(repo)}/commits",
        params={"path": path, "author": username, "per_page": 5},
    )
    return [commit["sha"] for commit in commits if commit.get("sha")]


def _commit_login(commit: dict[str, Any]) -> str | None:
    author = commit.get("author") or {}
    return author.get("login")


def _count_commits(
    client: httpx.Client, owner: str, repo: str, author: str | None = None
) -> int | None:
    """Count commits on the default branch using the Link header of a 1-per-page listing."""
    params: dict[str, Any] = {"per_page": 1}
    if author:
        params["author"] = author
    try:
        response = _get(
            client, f"{API_ROOT}/repos/{quote(owner)}/{quote(repo)}/commits", params=params
        )
    except GitHubIngestError:
        return None
    last = re.search(r'[?&]page=(\d+)>; rel="last"', response.headers.get("Link", ""))
    return int(last.group(1)) if last else len(response.json())


def _user_commits(
    client: httpx.Client, owner: str, repo: str, username: str
) -> tuple[list[dict[str, str]] | None, bool]:
    """The user's latest commits with author and committer dates; (None, False) when unavailable."""
    try:
        commits = _request(
            client,
            f"{API_ROOT}/repos/{quote(owner)}/{quote(repo)}/commits",
            params={"author": username, "per_page": HISTORY_PAGE_SIZE},
        )
    except GitHubRateLimitError:
        raise
    except GitHubIngestError:
        return None, False  # recorded as unknown
    if not isinstance(commits, list):
        return None, False
    result = []
    for commit in commits:
        details = commit.get("commit") or {}
        result.append({
            "sha": commit.get("sha", ""),
            "author_date": (details.get("author") or {}).get("date"),
            "committer_date": (details.get("committer") or {}).get("date"),
        })
    return result, len(commits) < HISTORY_PAGE_SIZE


def _commit_date(commit: dict[str, Any]) -> str | None:
    details = commit.get("commit") or {}
    return (details.get("author") or {}).get("date") or (details.get("committer") or {}).get("date")


def oldest_commit_date(client: httpx.Client, owner: str, repo: str, path: str) -> str | None:
    """When `path` first appeared in the repo: the date of its oldest commit, or None if unknown.

    Uses a 1-per-page listing and follows the Link rel="last" page; without a Link header the
    single commit returned is the oldest. Path history doesn't follow renames, so callers must
    treat None (or a later date) as "not earlier".
    """
    url = f"{API_ROOT}/repos/{quote(owner)}/{quote(repo)}/commits"
    try:
        response = _get(client, url, params={"path": path, "per_page": 1})
        last = re.search(r'[?&]page=(\d+)>; rel="last"', response.headers.get("Link", ""))
        if last:
            response = _get(client, url, params={"path": path, "per_page": 1, "page": int(last.group(1))})
        commits = response.json()
    except (GitHubIngestError, httpx.HTTPError, ValueError):
        return None
    if not isinstance(commits, list) or not commits:
        return None
    return _commit_date(commits[-1])


def _fetch_ci_status(
    client: httpx.Client, owner: str, repo: str, head_sha: str
) -> dict[str, Any] | None:
    """Fetch GitHub Check Runs on head_sha to verify actual CI run completion."""
    if not head_sha:
        return None
    try:
        data = _request(
            client,
            f"{API_ROOT}/repos/{quote(owner)}/{quote(repo)}/commits/{quote(head_sha)}/check-runs",
        )
    except GitHubRateLimitError:
        raise
    except GitHubIngestError:
        return None
    check_runs = data.get("check_runs", [])
    total = data.get("total_count", len(check_runs))
    if total == 0:
        return {
            "total_checks": 0,
            "successful_checks": 0,
            "failed_checks": 0,
            "in_progress_checks": 0,
            "has_successful_run": False,
            "status": "unavailable",
            "summary": "No CI check runs recorded for commit",
            "latest_conclusion": None,
        }

    completed_success = sum(
        1 for run in check_runs
        if run.get("status") == "completed" and run.get("conclusion") in ("success", "neutral")
    )
    failed_checks = sum(
        1 for run in check_runs
        if run.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required")
    )
    in_progress_checks = sum(
        1 for run in check_runs
        if run.get("status") in ("in_progress", "queued")
    )

    has_successful_run = total > 0 and failed_checks == 0 and in_progress_checks == 0 and completed_success == total

    if has_successful_run:
        status = "passed"
        summary = f"All {total} CI check(s) passed"
    elif failed_checks > 0:
        status = "failed"
        summary = f"{failed_checks} of {total} CI check(s) failed"
    elif in_progress_checks > 0:
        status = "in_progress"
        summary = f"{in_progress_checks} of {total} CI check(s) in progress"
    else:
        status = "partial"
        summary = f"{completed_success} of {total} CI check(s) passed"

    return {
        "total_checks": total,
        "successful_checks": completed_success,
        "failed_checks": failed_checks,
        "in_progress_checks": in_progress_checks,
        "has_successful_run": has_successful_run,
        "status": status,
        "summary": summary,
        "latest_conclusion": check_runs[0].get("conclusion") if check_runs else None,
    }


def _file_history(
    client: httpx.Client, owner: str, repo: str, path: str, username: str
) -> dict[str, Any] | None:
    """Who committed to this file, and how big was the commit that created it."""
    try:
        commits = _request(
            client,
            f"{API_ROOT}/repos/{quote(owner)}/{quote(repo)}/commits",
            params={"path": path, "per_page": HISTORY_PAGE_SIZE},
        )
    except GitHubIngestError:
        return None
    if not commits:
        return None
    complete = len(commits) < HISTORY_PAGE_SIZE
    by_user = sum(
        1 for commit in commits
        if (_commit_login(commit) or "").lower() == username.lower()
    )
    history: dict[str, Any] = {
        "total": len(commits),
        "complete": complete,
        "by_user": by_user,
        "creator_login": None,
        "creator_sha": None,
        "creation": None,
    }
    if not complete:
        return history
    creator = commits[-1]
    history["first_commit_date"] = _commit_date(creator)
    history["creator_login"] = _commit_login(creator)
    history["creator_sha"] = creator.get("sha")
    try:
        detail = _request(
            client,
            f"{API_ROOT}/repos/{quote(owner)}/{quote(repo)}/commits/{creator['sha']}",
        )
    except GitHubIngestError:
        return history
    files = detail.get("files") or []
    history["creation"] = {
        "files_changed": len(files),
        "total_additions": (detail.get("stats") or {}).get("additions", 0),
        "file_additions": next(
            (item.get("additions", 0) for item in files if item.get("filename") == path),
            None,
        ),
    }
    return history


BLAME_QUERY = """
query($owner: String!, $name: String!, $expr: String!, $path: String!) {
  repository(owner: $owner, name: $name) {
    object(expression: $expr) {
      ... on Commit {
        blame(path: $path) {
          ranges { startingLine endingLine commit { author { user { login } } } }
        }
      }
    }
  }
}
"""


def _file_blame(
    client: httpx.Client, owner: str, repo: str, sha: str, path: str
) -> list[list[Any]] | None:
    """Line ranges as [start, end, login]; login is None when the author isn't a linked GitHub user."""
    if not GITHUB_TOKEN or not sha:
        return None  # GitHub's GraphQL API (the only blame API) requires a token
    try:
        response = client.post(
            f"{API_ROOT}/graphql",
            json={
                "query": BLAME_QUERY,
                "variables": {"owner": owner, "name": repo, "expr": sha, "path": path},
            },
        )
        response.raise_for_status()
        data = response.json()
        ranges = data["data"]["repository"]["object"]["blame"]["ranges"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    return [
        [
            item["startingLine"],
            item["endingLine"],
            (((item.get("commit") or {}).get("author") or {}).get("user") or {}).get("login"),
        ]
        for item in ranges
    ]


def ingest_github(username: str, force_refresh: bool = False) -> dict[str, Any]:
    username = validate_github_username(username)

    cache_path = _cache_path(username)
    if not force_refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            cache_path.unlink(missing_ok=True)
        else:
            if isinstance(cached, dict):
                cached_at = cached.get("cached_at")
                fresh = (
                    isinstance(cached_at, (int, float))
                    and time.time() - cached_at < CACHE_TTL_SECONDS
                )
                if (
                    fresh
                    and cached.get("schema_version") == CACHE_SCHEMA_VERSION
                    and isinstance(cached.get("data"), dict)
                ):
                    return cached["data"]
            cache_path.unlink(missing_ok=True)

    # When this ingestion fetched the files; recorded on every evidence record.
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    timeout = httpx.Timeout(20.0, connect=10.0)
    with httpx.Client(headers=_headers(), timeout=timeout, follow_redirects=True) as client:
        profile = _request(client, f"{API_ROOT}/users/{quote(username)}")
        repositories = _request(
            client,
            f"{API_ROOT}/users/{quote(username)}/repos",
            params={"type": "owner", "sort": "pushed", "direction": "desc", "per_page": 100},
        )
        eligible = [
            repo for repo in repositories
            if not repo.get("fork") and repo.get("visibility", "public") == "public"
        ]
        skipped_repos = [
            {"name": repo.get("name", "?"), "reason": "fork" if repo.get("fork") else "not public"}
            for repo in repositories if repo not in eligible
        ]
        eligible.sort(key=lambda repo: repo.get("pushed_at") or "", reverse=True)
        skipped_repos += [
            {"name": repo.get("name", "?"), "reason": f"not among the {MAX_REPOS} most recently pushed"}
            for repo in eligible[MAX_REPOS:]
        ]
        repo_results: list[dict[str, Any]] = []

        for repo in eligible[:MAX_REPOS]:
            owner = repo["owner"]["login"]
            name = repo["name"]
            branch = repo.get("default_branch") or "main"
            if not repo.get("default_branch"):
                tree_response = {"tree": [], "truncated": False}
                head_sha = ""
                head_verification = None
            else:
                try:
                    branch_commit = _request(
                        client,
                        f"{API_ROOT}/repos/{quote(owner)}/{quote(name)}/commits/{quote(branch, safe='')}",
                    )
                    head_sha = branch_commit.get("sha", "")
                    head_verification = (branch_commit.get("commit") or {}).get("verification")
                    tree_response = _request(
                        client,
                        f"{API_ROOT}/repos/{quote(owner)}/{quote(name)}/git/trees/{quote(branch, safe='')}",
                        params={"recursive": "1"},
                    )
                except GitHubRateLimitError:
                    raise
                except GitHubIngestError as exc:
                    # Empty (409), missing (404), blocked (451) or broken repo: skip it.
                    skipped_repos.append({"name": name, "reason": _repo_error_reason(exc)})
                    continue
            tree = [
                item["path"]
                for item in tree_response.get("tree", [])
                if item.get("type") == "blob"
            ]
            tree_set = set(tree)
            dependency_paths = [
                path for path in tree
                if Path(path).name.lower() in DEPENDENCY_FILES
            ]
            configs = [
                path for path in tree
                if Path(path).name in DOCKER_FILES
                or path.startswith(CI_DIRS)
                or path in CI_FILES
                or path == ".gitignore"
                or path.startswith("k8s/")
                or path.startswith("kubernetes/")
                or path in {"next.config.js", "next.config.ts"}
            ]
            tests = [path for path in tree if is_test_path(path)]
            dependency_contents = {}
            for path in dependency_paths:
                content = _optional_content(client, owner, name, path, branch)
                if content is not None:
                    dependency_contents[path] = content
            config_contents = {}
            for path in configs:
                blob = next(
                    (
                        item for item in tree_response.get("tree", [])
                        if item.get("path") == path and item.get("type") == "blob"
                    ),
                    None,
                )
                if blob and blob.get("size", MAX_FILE_BYTES + 1) <= MAX_FILE_BYTES:
                    content = _optional_content(client, owner, name, path, branch)
                    if content is not None:
                        config_contents[path] = content

            user_commits, user_commits_complete = _user_commits(client, owner, name, username)
            recent = _recently_changed_paths(client, owner, name, user_commits or [])
            sizes = {
                item["path"]: item.get("size", MAX_FILE_BYTES + 1)
                for item in tree_response.get("tree", []) if item.get("type") == "blob"
            }
            skip_reasons: dict[str, int] = {}
            selected = _select_source_files(
                _ordered_candidates(tree, recent), sizes,
                lambda path: _optional_content(client, owner, name, path, branch),
                skip_reasons,
            )
            source_files: dict[str, dict[str, Any]] = {}
            for path, (content, lines) in selected.items():
                try:
                    commits = _file_authorship(client, owner, name, path, username)
                except GitHubRateLimitError:
                    raise
                except GitHubIngestError:
                    commits = []  # authorship unknown: the claim's "authored" check fails
                history = _file_history(client, owner, name, path, username)
                source_files[path] = {
                    "content": content,
                    "lines": lines,
                    "authored_shas": commits,
                    "line_count": len(lines),
                    "history": history,
                    "blame": _file_blame(client, owner, name, head_sha, path),
                    "commit_verification": head_verification,
                    "commit_verification_scope": "head",
                    # When this file first appeared here; compared with matches found elsewhere.
                    "first_commit_date": (history or {}).get("first_commit_date")
                    or (oldest_commit_date(client, owner, name, path) if history else None),
                    "retrieved_at": retrieved_at,
                }

            authored_paths = _authored_paths(
                client, owner, name, [*dependency_paths, *config_contents], username
            )
            test_contents = _test_contents(
                tests, source_files, sizes, lambda path: _optional_content(client, owner, name, path, branch),
            )
            test_authored = _test_authorship(client, owner, name, list(test_contents), source_files, username)
            has_ci_config = any(path.startswith(CI_DIRS) or path in CI_FILES for path in configs)
            ci_status = _fetch_ci_status(client, owner, name, head_sha) if has_ci_config else None
            repo_results.append(
                {
                    "name": name,
                    "full_name": repo["full_name"],
                    "owner": owner,
                    "default_branch": branch,
                    "head_sha": head_sha,
                    "commit_verification": head_verification,
                    "commit_verification_scope": "head",
                    "ci_status": ci_status,
                    "pushed_at": repo.get("pushed_at"),
                    "created_at": repo.get("created_at"),
                    "html_url": repo.get("html_url"),
                    "is_fork": bool(repo.get("fork")),
                    "tree": tree,
                    "tree_truncated": tree_response.get("truncated", False),
                    "dependency_files": dependency_paths,
                    "dependency_contents": dependency_contents,
                    "config_files": configs,
                    "config_contents": config_contents,
                    "test_files": tests,
                    "test_contents": test_contents,
                    "test_authored": test_authored,
                    "source_files": source_files,
                    "authored_paths": authored_paths,
                    "source_file_count": sum(1 for path in tree if _source_candidate(path)),
                    "coverage": _repo_coverage(tree, source_files, skip_reasons, tree_response),
                    "user_commits": user_commits,
                    "user_commits_complete": user_commits_complete,
                    "commit_counts": {
                        "total": _count_commits(client, owner, name),
                        # A complete commit list already gives the user's count.
                        "by_user": (
                            len(user_commits) if user_commits_complete and user_commits is not None
                            else _count_commits(client, owner, name, author=username)
                        ),
                    },
                }
            )

    result = {
        "username": profile["login"],
        "profile_url": profile.get("html_url"),
        "repo_count": len(repo_results),
        "repos": repo_results,
        "repos_listed": len(repositories),
        "skipped_repos": skipped_repos,
    }
    write_json_cache(
        cache_path,
        {"schema_version": CACHE_SCHEMA_VERSION, "cached_at": time.time(), "data": result},
    )
    return result
