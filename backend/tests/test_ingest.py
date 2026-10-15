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


def _code(lines: int) -> str:
    return "\n".join(f"value_{index} = {index}" for index in range(lines))


def test_file_changed_in_latest_commit_is_selected_even_if_it_sorts_last(fake_github: FakeGitHub) -> None:
    files = {f"{letter}.py": _code(30) for letter in "abcdefgh"}
    files["zzz/late.py"] = _code(30)
    fake_github.add_repo(
        "project", files,
        user_commits=[user_commit("newest"), user_commit("older")],
        commit_files={"newest": ["zzz/late.py", "README.md"], "older": ["b.py"]},
    )
    sources = list(github_ingest.ingest_github("student")["repos"][0]["source_files"])
    assert len(sources) == github_ingest.MAX_SOURCE_FILES == 6
    assert sources[:2] == ["zzz/late.py", "b.py"]  # most recent change first
    assert "h.py" not in sources


def test_a_450_line_file_is_now_included(fake_github: FakeGitHub) -> None:
    fake_github.add_repo("project", {"app/service.py": _code(450)})
    source = github_ingest.ingest_github("student")["repos"][0]["source_files"]["app/service.py"]
    assert source["line_count"] == 450


def test_longer_files_are_preferred_and_huge_files_are_not_fetched(fake_github: FakeGitHub) -> None:
    files = {f"{letter}.py": _code(20) for letter in "bcdefg"}
    files["a.py"] = _code(3)  # sorts first, but is too short to be useful evidence
    files["huge.py"] = _code(10)
    fake_github.add_repo("project", files, sizes={"huge.py": 500_000})
    sources = github_ingest.ingest_github("student")["repos"][0]["source_files"]
    assert "a.py" not in sources and len(sources) == 6
    assert not any(call.endswith("contents/huge.py") for call in fake_github.calls)


def test_short_files_fill_remaining_slots() -> None:
    fetched = github_ingest._select_source_files(
        ["a.py", "b.py"], {"a.py": 10, "b.py": 10}, lambda path: {"a.py": _code(3), "b.py": _code(20)}[path],
    )
    assert list(fetched) == ["b.py", "a.py"]


def test_prompt_line_cap_drops_the_lowest_priority_files_first() -> None:
    from app.services.claim_proposer import _files_for_prompt

    def source(lines: int) -> dict:
        return {"lines": ["x"] * lines}

    ingestion = {"repos": [
        {"name": "recent", "source_files": {"r1.py": source(400), "r2.py": source(400), "r3.py": source(400)}},
        {"name": "older", "source_files": {"o1.py": source(400), "o2.py": source(400)}},
    ]}
    kept = [(repo, path) for repo, path, _ in _files_for_prompt(ingestion, max_lines=1600)]
    # Priority: r1, o1, r2, o2, r3 -> r3, the lowest priority, no longer fits and is dropped.
    assert kept == [("recent", "r1.py"), ("recent", "r2.py"), ("older", "o1.py"), ("older", "o2.py")]
    kept = [path for _, path, _ in _files_for_prompt(ingestion, max_lines=1000)]
    assert kept == ["r1.py", "o1.py"]


def test_authored_paths_marks_files_the_user_committed_to(monkeypatch) -> None:
    def authorship(client, owner, repo, path, username):
        if path == "broken.yml":
            raise GitHubIngestError("GitHub API returned 500: oops")
        return ["sha1"] if path in {"requirements.txt", "Dockerfile"} else []

    monkeypatch.setattr(github_ingest, "_file_authorship", authorship)
    result = github_ingest._authored_paths(
        object(), "student", "demo",
        ["requirements.txt", "package.json", "Dockerfile", "tests/test_app.py", "broken.yml", "Dockerfile"],
        "student",
    )
    assert result == {"requirements.txt": True, "package.json": False, "Dockerfile": True}

    def limited(*_args):
        raise GitHubRateLimitError("GitHub API rate limit exceeded")

    monkeypatch.setattr(github_ingest, "_file_authorship", limited)
    with pytest.raises(GitHubRateLimitError):
        github_ingest._authored_paths(object(), "student", "demo", ["Dockerfile"], "student")


def test_cache_schema_is_version_3() -> None:
    assert github_ingest.CACHE_SCHEMA_VERSION == 3


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
