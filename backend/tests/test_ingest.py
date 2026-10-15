import base64
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import github_ingest
from app.services.github_ingest import GitHubIngestError, GitHubRateLimitError

API = github_ingest.API_ROOT


class FakeResponse:
    def __init__(self, data: Any) -> None:
        self._data = data
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self._data


class FakeGitHub:
    """Answers the GitHub REST calls made by ingest_github for one user, without a network."""

    def __init__(self, username: str = "student") -> None:
        self.username = username
        self.repos: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []

    def add_repo(
        self, name: str, files: dict[str, str] | None = None, *, error: Exception | None = None,
        authored: set[str] | None = None, user_commits: list[dict] | None = None,
        commit_files: dict[str, list[str]] | None = None, sizes: dict[str, int] | None = None,
    ) -> None:
        self.repos[name] = {
            "files": files or {}, "error": error, "authored": authored or set(),
            "user_commits": user_commits or [], "commit_files": commit_files or {}, "sizes": sizes or {},
        }

    def get(self, client: Any, url: str, **kwargs: Any) -> FakeResponse:
        params = kwargs.get("params") or {}
        self.calls.append(url)
        user = self.username
        if url == f"{API}/users/{user}":
            return FakeResponse({"login": user, "html_url": f"https://github.com/{user}"})
        if url == f"{API}/users/{user}/repos":
            return FakeResponse([
                {"name": name, "full_name": f"{user}/{name}", "owner": {"login": user},
                 "default_branch": "main", "fork": False, "visibility": "public",
                 "pushed_at": f"2024-0{index + 1}-01T00:00:00Z", "created_at": "2023-01-01T00:00:00Z"}
                for index, name in enumerate(self.repos)
            ])
        prefix = f"{API}/repos/{user}/"
        name, _, rest = url[len(prefix):].partition("/")
        repo = self.repos[name]
        if rest == "commits/main":
            if repo["error"]:
                raise repo["error"]
            return FakeResponse({"sha": f"head-{name}"})
        if rest == "git/trees/main":
            return FakeResponse({"tree": [
                {"path": path, "type": "blob", "size": repo["sizes"].get(path, len(text))}
                for path, text in repo["files"].items()
            ], "truncated": False})
        if rest.startswith("contents/"):
            text = repo["files"][rest[len("contents/"):]]
            return FakeResponse({"encoding": "base64", "content": base64.b64encode(text.encode()).decode()})
        if rest.startswith("commits/"):
            sha = rest[len("commits/"):]
            return FakeResponse({"files": [{"filename": path} for path in repo["commit_files"].get(sha, [])]})
        if rest == "commits":
            if "path" in params:
                if "author" in params:
                    return FakeResponse([{"sha": "a1"}] if params["path"] in repo["authored"] else [])
                return FakeResponse([])  # file history: unknown
            if "author" in params and params.get("per_page") != 1:
                return FakeResponse(repo["user_commits"])
            return FakeResponse([{"sha": "x"}])  # commit count
        raise AssertionError(f"unexpected GitHub call: {url}")


@pytest.fixture
def fake_github(monkeypatch, tmp_path) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(github_ingest, "_get", fake.get)
    monkeypatch.setattr(github_ingest, "_file_blame", lambda *args: None)
    monkeypatch.setattr(github_ingest, "GITHUB_TOKEN", "")
    monkeypatch.setattr(github_ingest, "_cache_path", lambda username: tmp_path / f"github-{username}.json")
    return fake


def user_commit(sha: str, date: str = "2024-01-01T00:00:00Z") -> dict:
    return {"sha": sha, "commit": {"author": {"date": date}, "committer": {"date": date}}}


def test_empty_repo_is_skipped_and_the_normal_repo_is_used(fake_github: FakeGitHub) -> None:
    fake_github.add_repo("empty", error=GitHubIngestError("GitHub API returned 409: Git Repository is empty."))
    fake_github.add_repo("normal", {"app.py": "import os\n" * 20}, authored={"app.py"})
    result = github_ingest.ingest_github("student")
    assert [repo["name"] for repo in result["repos"]] == ["normal"]
    assert "app.py" in result["repos"][0]["source_files"]


@pytest.mark.parametrize("status", [404, 451, 500])
def test_other_repo_errors_skip_the_repo(fake_github: FakeGitHub, status: int) -> None:
    fake_github.add_repo("broken", error=GitHubIngestError(f"GitHub API returned {status}: nope"))
    fake_github.add_repo("normal", {"app.py": "import os\n"})
    assert [repo["name"] for repo in github_ingest.ingest_github("student")["repos"]] == ["normal"]


def test_rate_limit_still_fails_the_job(fake_github: FakeGitHub) -> None:
    fake_github.add_repo("limited", error=GitHubRateLimitError("GitHub API rate limit exceeded"))
    with pytest.raises(GitHubRateLimitError):
        github_ingest.ingest_github("student")


def test_user_commit_fetch_errors_are_recorded_as_unknown(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise GitHubIngestError("GitHub API returned 409: Git Repository is empty.")

    monkeypatch.setattr(github_ingest, "_request", fail)
    assert github_ingest._user_commits(object(), "student", "empty", "student") == (None, False)

    def limited(*_args, **_kwargs):
        raise GitHubRateLimitError("GitHub API rate limit exceeded")

    monkeypatch.setattr(github_ingest, "_request", limited)
    with pytest.raises(GitHubRateLimitError):
        github_ingest._user_commits(object(), "student", "empty", "student")


def test_only_real_rate_limits_are_rate_limit_errors() -> None:
    class Response:
        def __init__(self, status: int, message: str, remaining: str) -> None:
            self.status_code, self.reason_phrase = status, "Forbidden"
            self.is_error = status >= 400
            self.headers = {"X-RateLimit-Remaining": remaining}
            self._message = message

        def json(self) -> dict:
            return {"message": self._message}

    def call(response: Response) -> None:
        github_ingest._get(type("C", (), {"get": lambda self, url, **kw: response})(), "u")

    with pytest.raises(GitHubRateLimitError):
        call(Response(403, "API rate limit exceeded", "0"))
    with pytest.raises(GitHubRateLimitError):
        call(Response(429, "Too many requests", "10"))
    with pytest.raises(GitHubIngestError) as error:
        call(Response(403, "Repository access blocked", "4000"))
    assert not isinstance(error.value, GitHubRateLimitError)
