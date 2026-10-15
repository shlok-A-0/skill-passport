import base64
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.config import CACHE_DIR, GITHUB_TOKEN

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
MAX_LINES = 300
MAX_SOURCE_FILES = 4
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
CACHE_SCHEMA_VERSION = 2


class GitHubIngestError(RuntimeError):
    pass


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
        raise GitHubIngestError(f"GitHub resource not found: {url}")
    if response.status_code in (403, 429):
        remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
        reset = response.headers.get("X-RateLimit-Reset")
        detail = "GitHub API rate limit exceeded"
        if reset:
            detail += f"; reset at Unix time {reset}"
        raise GitHubIngestError(f"{detail} (remaining={remaining})")
    if response.is_error:
        try:
            message = response.json().get("message", response.reason_phrase)
        except ValueError:
            message = response.reason_phrase
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
        response = client.get(download_url)
        response.raise_for_status()
        return response.text
    return ""


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
    except GitHubIngestError:
        return None, False
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
    username = username.strip()
    if not username or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", username):
        raise ValueError("GitHub username must be 1–39 letters, numbers, or hyphens.")

    cache_path = _cache_path(username)
    if not force_refresh and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        fresh = time.time() - cached.get("cached_at", 0) < CACHE_TTL_SECONDS
        if fresh and cached.get("schema_version") == CACHE_SCHEMA_VERSION:
            return cached["data"]

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
        eligible.sort(key=lambda repo: repo.get("pushed_at") or "", reverse=True)
        repo_results: list[dict[str, Any]] = []

        for repo in eligible[:5]:
            owner = repo["owner"]["login"]
            name = repo["name"]
            branch = repo.get("default_branch") or "main"
            if not repo.get("default_branch"):
                tree_response = {"tree": [], "truncated": False}
                head_sha = ""
            else:
                try:
                    branch_commit = _request(
                        client,
                        f"{API_ROOT}/repos/{quote(owner)}/{quote(name)}/commits/{quote(branch, safe='')}",
                    )
                    head_sha = branch_commit.get("sha", "")
                    tree_response = _request(
                        client,
                        f"{API_ROOT}/repos/{quote(owner)}/{quote(name)}/git/trees/{quote(branch, safe='')}",
                        params={"recursive": "1"},
                    )
                except GitHubIngestError as exc:
                    if "resource not found" not in str(exc):
                        raise
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
                dependency_contents[path] = _get_file_content(client, owner, name, path, branch)
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
                    content = _get_file_content(client, owner, name, path, branch)
                    config_contents[path] = content

            candidates = sorted(
                (path for path in tree if _source_candidate(path)),
                key=_candidate_priority,
            )
            # Reserve one of the file slots for a test file, so testing skill can be
            # cited in code rather than only inferred from the presence of test files.
            test_candidates = sorted(
                (
                    path for path in candidates
                    if is_test_path(path) and Path(path).name != "__init__.py"
                ),
                key=lambda path: (
                    not _named_like_test(path),
                    not set(path.lower().split("/")[:-1]) & TEST_DIRS,
                    _candidate_priority(path),
                ),
            )[:10]
            candidates = test_candidates + [
                path for path in candidates if not is_test_path(path)
            ]
            source_files: dict[str, dict[str, Any]] = {}
            for path in candidates:
                if len(source_files) >= MAX_SOURCE_FILES:
                    break
                if is_test_path(path) and any(is_test_path(taken) for taken in source_files):
                    continue
                blob = next(
                    (
                        item for item in tree_response.get("tree", [])
                        if item.get("path") == path and item.get("type") == "blob"
                    ),
                    None,
                )
                if not blob or blob.get("size", MAX_FILE_BYTES + 1) > MAX_FILE_BYTES:
                    continue
                content = _get_file_content(client, owner, name, path, branch)
                lines = content.splitlines()
                if lines and len(lines) > MAX_LINES and is_test_path(path):
                    # Long test files: keep the first lines. Line numbers stay identical
                    # to GitHub's, so citations inside them remain exact.
                    lines = lines[:MAX_LINES]
                    content = "\n".join(lines)
                if not lines or len(lines) > MAX_LINES:
                    continue
                commits = _file_authorship(client, owner, name, path, username)
                source_files[path] = {
                    "content": content,
                    "lines": lines,
                    "authored_shas": commits,
                    "line_count": len(lines),
                    "history": _file_history(client, owner, name, path, username),
                    "blame": _file_blame(client, owner, name, head_sha, path),
                }

            user_commits, user_commits_complete = _user_commits(client, owner, name, username)
            repo_results.append(
                {
                    "name": name,
                    "full_name": repo["full_name"],
                    "owner": owner,
                    "default_branch": branch,
                    "head_sha": head_sha,
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
                    "source_files": source_files,
                    "source_file_count": sum(1 for path in tree if _source_candidate(path)),
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
    }
    cache_path.write_text(
        json.dumps({"schema_version": CACHE_SCHEMA_VERSION, "cached_at": time.time(), "data": result}),
        encoding="utf-8",
    )
    return result
