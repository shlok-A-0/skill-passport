"""Codeforces evidence: official public API mapping, caching and failure modes."""

import json

import pytest

from app.services import codeforces_ingest
from app.services.codeforces_ingest import (
    CodeforcesIngestError,
    CodeforcesUserNotFoundError,
    fetch_codeforces,
    validate_codeforces_handle,
)


def _user(handle="tourist"):
    return {
        "handle": handle,
        "rating": 3000,
        "maxRating": 3979,
        "rank": "legendary grandmaster",
        "maxRank": "legendary grandmaster",
        "contribution": 150,
        "registrationTimeSeconds": 1_280_000_000,
    }


def _submission(sub_id, verdict, rating=None, tags=(), language="C++20"):
    problem = {"contestId": 1000 + sub_id, "index": "A", "tags": list(tags)}
    if rating is not None:
        problem["rating"] = rating
    return {
        "id": sub_id,
        "contestId": problem["contestId"],
        "verdict": verdict,
        "programmingLanguage": language,
        "problem": problem,
    }


def _stub(monkeypatch, tmp_path, users=None, rating=None, submissions=None):
    calls = []

    def fake_call(client, method, params, handle):
        calls.append(method)
        if method == "user.info":
            return users if users is not None else [_user()]
        if method == "user.rating":
            return rating if rating is not None else [
                {"contestId": 1, "ratingUpdateTimeSeconds": 1_700_000_000,
                 "oldRating": 2900, "newRating": 3000},
            ]
        if method == "user.status":
            return submissions if submissions is not None else [
                _submission(1, "OK", rating=800, tags=["implementation", "math"]),
                _submission(2, "OK", rating=1600, tags=["dp"]),
                _submission(3, "WRONG_ANSWER", rating=1200, tags=["dp"]),
                _submission(2, "OK", rating=1600, tags=["dp"]),  # retry: same problem
            ]
        raise AssertionError(method)

    monkeypatch.setattr(codeforces_ingest, "_call", fake_call)
    monkeypatch.setattr(
        codeforces_ingest, "_cache_path", lambda handle: tmp_path / f"cf-{handle}.json"
    )
    return calls


def test_fetch_maps_profile_and_aggregates(monkeypatch, tmp_path):
    calls = _stub(monkeypatch, tmp_path)
    result = fetch_codeforces("tourist")
    assert calls == ["user.info", "user.rating", "user.status"]
    assert result["handle"] == "tourist"
    assert result["ownership"] == "unverified"
    assert result["profile"]["rating"] == 3000
    assert result["profile"]["max_rating"] == 3979
    assert result["rated_contests"] == 1
    assert result["last_rated_contest_at"] is not None
    # Unique solved problems only: the failed attempt and the retry don't double-count.
    assert result["solved_count"] == 2
    assert result["solved_max_problem_rating"] == 1600
    assert ["dp", 1] in result["top_tags"] and ["math", 1] in result["top_tags"]
    assert result["submission_sample"] == codeforces_ingest.SUBMISSION_SAMPLE


def test_fetch_serves_cache_for_same_handle(monkeypatch, tmp_path):
    calls = _stub(monkeypatch, tmp_path)
    first = fetch_codeforces("tourist")
    second = fetch_codeforces("tourist")
    assert first == second
    assert calls == ["user.info", "user.rating", "user.status"]  # no refetch


def test_fetch_force_refresh_bypasses_cache(monkeypatch, tmp_path):
    calls = _stub(monkeypatch, tmp_path)
    fetch_codeforces("tourist")
    fetch_codeforces("tourist", force_refresh=True)
    assert calls == ["user.info", "user.rating", "user.status"] * 2


def test_unknown_handle_raises_not_found(monkeypatch, tmp_path):
    def fake_call(client, method, params, handle):
        raise CodeforcesUserNotFoundError(f"No Codeforces user '{handle}'.")

    monkeypatch.setattr(codeforces_ingest, "_call", fake_call)
    monkeypatch.setattr(
        codeforces_ingest, "_cache_path", lambda handle: tmp_path / f"cf-{handle}.json"
    )
    with pytest.raises(CodeforcesUserNotFoundError):
        fetch_codeforces("no-such-user-xyz")


def test_platform_failure_raises_ingest_error(monkeypatch, tmp_path):
    def fake_call(client, method, params, handle):
        raise CodeforcesIngestError("Codeforces answered HTTP 503.")

    monkeypatch.setattr(codeforces_ingest, "_call", fake_call)
    monkeypatch.setattr(
        codeforces_ingest, "_cache_path", lambda handle: tmp_path / f"cf-{handle}.json"
    )
    with pytest.raises(CodeforcesIngestError):
        fetch_codeforces("tourist")


def test_handle_validation():
    assert validate_codeforces_handle("  tourist ") == "tourist"
    for bad in ("", "has space", "x" * 25, "semi;colon"):
        with pytest.raises(ValueError):
            validate_codeforces_handle(bad)


class _FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = 0

    def get(self, url, params=None):
        self.requests += 1
        return _FakeResponse(self.payloads.pop(0))


def test_call_retries_once_on_call_limit_exceeded(monkeypatch):
    monkeypatch.setattr(codeforces_ingest, "_pace", lambda: None)
    monkeypatch.setattr(codeforces_ingest.time, "sleep", lambda _: None)
    client = _FakeClient([
        {"status": "FAILED", "comment": "Call limit exceeded"},
        {"status": "OK", "result": [{"handle": "tourist"}]},
    ])
    result = codeforces_ingest._call(client, "user.info", {}, "tourist")
    assert result == [{"handle": "tourist"}]
    assert client.requests == 2


def test_call_gives_up_after_one_limit_retry(monkeypatch):
    monkeypatch.setattr(codeforces_ingest, "_pace", lambda: None)
    monkeypatch.setattr(codeforces_ingest.time, "sleep", lambda _: None)
    client = _FakeClient([
        {"status": "FAILED", "comment": "Call limit exceeded"},
        {"status": "FAILED", "comment": "Call limit exceeded"},
    ])
    try:
        codeforces_ingest._call(client, "user.info", {}, "tourist")
        raised = False
    except CodeforcesIngestError:
        raised = True
    assert raised
    assert client.requests == 2


def test_pace_enforces_minimum_interval(monkeypatch):
    sleeps = []
    monkeypatch.setattr(codeforces_ingest.time, "sleep", sleeps.append)
    monkeypatch.setattr(codeforces_ingest, "_last_request_at", codeforces_ingest.time.monotonic())
    codeforces_ingest._pace()
    assert sleeps and sleeps[0] > 0


def test_sparse_profile_reports_actual_examined_count(monkeypatch, tmp_path):
    _stub(monkeypatch, tmp_path, submissions=[_submission(1, "OK", rating=800, tags=["math"])])
    result = fetch_codeforces("newcomer")
    assert result["submission_sample"] == codeforces_ingest.SUBMISSION_SAMPLE
    assert result["submissions_examined"] == 1
    assert result["solved_count"] == 1
