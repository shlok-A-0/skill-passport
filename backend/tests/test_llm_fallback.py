import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config
from app.services import gemini
from google.genai import errors


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr(config, "DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setattr(gemini.time, "sleep", lambda seconds: None)


class FakeModels:
    def __init__(self, error=None, text='{"claims": []}'):
        self.error = error
        self.text = text
        self.calls = 0

    def generate_content(self, model, contents, config):
        self.calls += 1
        if self.error:
            raise self.error
        return type("R", (), {"text": self.text})()


def client_with(models):
    return type("C", (), {"models": models})()


def test_gemini_success_never_calls_deepseek(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gemini, "_deepseek_request",
                        lambda contents: pytest.fail("DeepSeek must not be called"))
    models = FakeModels()
    assert gemini.generate_json(client_with(models), "prompt") == '{"claims": []}'
    assert models.calls == 1


def test_gemini_quota_exhaustion_falls_back_to_deepseek(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    quota = errors.ClientError(429, {"error": {"message": "quota"}})
    seen = []
    monkeypatch.setattr(gemini, "_deepseek_request",
                        lambda contents: seen.append(contents) or '{"claims": [1]}')
    result = gemini.generate_json(client_with(FakeModels(error=quota)), "prompt")
    assert result == '{"claims": [1]}'
    assert seen == ["prompt"]  # one fallback attempt, same contents


def test_gemini_failure_without_deepseek_key_raises_original(monkeypatch):
    quota = errors.ClientError(429, {"error": {"message": "quota"}})
    with pytest.raises(errors.ClientError):
        gemini.generate_json(client_with(FakeModels(error=quota)), "prompt")


def test_prefer_deepseek_skips_gemini_entirely(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    models = FakeModels()
    monkeypatch.setattr(gemini, "_deepseek_request", lambda contents: "{}")
    assert gemini.generate_json(client_with(models), "prompt", prefer="deepseek") == "{}"
    assert models.calls == 0


def test_prefer_deepseek_without_key_is_unavailable():
    with pytest.raises(gemini.GeminiUnavailableError):
        gemini.generate_json(client_with(FakeModels()), "prompt", prefer="deepseek")


def test_deepseek_request_shape(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sekret")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(gemini.httpx, "post", fake_post)
    assert gemini.deepseek_generate_json("prompt") == '{"ok": true}'
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sekret"
    assert captured["json"]["model"] == "deepseek-flash"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["json"]["temperature"] == 0
    assert captured["json"]["stream"] is False


def test_deepseek_retries_then_raises_unavailable(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(url)
        request = httpx.Request("POST", url)
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("limited", request=request, response=response)

    monkeypatch.setattr(gemini.httpx, "post", fake_post)
    with pytest.raises(gemini.GeminiUnavailableError):
        gemini.deepseek_generate_json("prompt")
    assert len(calls) == 1 + len(gemini.DEEPSEEK_RETRY_DELAYS_SECONDS)


def test_deepseek_permanent_error_not_retried(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(url)
        request = httpx.Request("POST", url)
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    monkeypatch.setattr(gemini.httpx, "post", fake_post)
    with pytest.raises(gemini.GeminiUnavailableError):
        gemini.deepseek_generate_json("prompt")
    assert len(calls) == 1


def test_no_fallback_on_bad_request_or_auth(monkeypatch):
    """400/401 are not transient: falling back would mask a real bug or a bad key."""
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gemini, "_deepseek_request",
                        lambda contents: pytest.fail("DeepSeek must not be called"))
    for code in (400, 401, 403):
        error = errors.ClientError(code, {"error": {"message": "bad"}})
        with pytest.raises(errors.ClientError):
            gemini.generate_json(client_with(FakeModels(error=error)), "prompt")


def test_fallback_ignores_gemini_model_names(monkeypatch):
    """The JD path passes GEMINI_JD_MODEL; DeepSeek must still get its own model."""
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(config, "DEEPSEEK_MODEL", "deepseek-flash")
    seen = []
    monkeypatch.setattr(gemini, "_deepseek_request", lambda contents: seen.append(contents) or "{}")
    quota = errors.ClientError(429, {"error": {"message": "quota"}})
    gemini.generate_json(client_with(FakeModels(error=quota)), "prompt",
                         model="gemini-3.5-flash-lite")
    assert seen == ["prompt"]


def test_deepseek_request_disables_thinking(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(gemini.httpx, "post",
                        lambda url, headers, json, timeout: captured.update(json=json) or FakeResponse())
    gemini.deepseek_generate_json("prompt")
    assert captured["json"]["thinking"] == {"type": "disabled"}


def test_provider_provenance_tracked(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(gemini, "_deepseek_request", lambda contents: "{}")
    result = gemini.generate_json_result(client_with(FakeModels()), "prompt")
    assert result.provider == "gemini"
    quota = errors.ClientError(429, {"error": {"message": "quota"}})
    result = gemini.generate_json_result(client_with(FakeModels(error=quota)), "prompt")
    assert result.provider == "deepseek"


def test_provenance_is_per_call_under_concurrency(monkeypatch):
    """Interleaved calls must each carry their own provider - a shared mutable
    "last provider" would let one request's fallback answer be cached under
    another request's primary key."""
    import threading

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    entered = threading.Event()
    release = threading.Event()

    def slow_deepseek(contents):
        entered.set()
        assert release.wait(timeout=5)
        return "{}"

    monkeypatch.setattr(gemini, "_deepseek_request", slow_deepseek)
    quota = errors.ClientError(429, {"error": {"message": "quota"}})
    outcomes = {}

    def fallback_call():
        outcomes["fallback"] = gemini.generate_json_result(
            client_with(FakeModels(error=quota)), "prompt")

    thread = threading.Thread(target=fallback_call)
    thread.start()
    assert entered.wait(timeout=5)
    # While the fallback call is blocked inside DeepSeek, a normal call completes.
    outcomes["primary"] = gemini.generate_json_result(client_with(FakeModels()), "prompt")
    release.set()
    thread.join(timeout=5)
    assert outcomes["primary"].provider == "gemini"
    assert outcomes["fallback"].provider == "deepseek"


def test_fallback_answer_not_cached(monkeypatch, tmp_path):
    """A DeepSeek outage answer must not be served later as if Gemini produced it."""
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(gemini, "_deepseek_request", lambda contents: '{"reqs": ["x"]}')
    quota = errors.ClientError(429, {"error": {"message": "quota"}})
    monkeypatch.setattr(gemini.genai, "Client",
                        lambda api_key: client_with(FakeModels(error=quota)))
    items = gemini.propose_json_items("prompt", "reqs", "jd")
    assert items == ["x"]
    assert list(tmp_path.glob("jd-*.json")) == []
