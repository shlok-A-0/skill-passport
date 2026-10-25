import dataclasses
import hashlib
import json
import logging
import re
import time
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from app import config
from app.config import GEMINI_MODEL
from app.services.json_cache import write_json_cache

logger = logging.getLogger(__name__)


class GeminiUnavailableError(RuntimeError):
    """Gemini isn't configured, or the call failed after its retries."""


class GeminiOutputError(ValueError):
    """Gemini answered, but not with the requested JSON (after one retry)."""
RETRY_DELAYS_SECONDS = (3, 8, 20)
# Dropped connections and timeouts (httpx.RemoteProtocolError, ReadTimeout, ConnectError, resets).
NETWORK_RETRY_DELAYS_SECONDS = (3, 8)
NETWORK_ERRORS = (httpx.TransportError, ConnectionError)


def generate_json(client: genai.Client, contents: str, model: str | None = None,
                  prefer: str = "gemini") -> str:
    """One JSON generation call. Gemini is always primary: it runs first with its own
    retries, and DeepSeek is tried only after Gemini fails terminally (quota, outage or
    connection exhausted). `prefer="deepseek"` sends this one call straight to the
    fallback - used by callers' invalid-output retries so a malformed Gemini answer is
    not simply re-asked of the same model. Semantic correctness is NOT a trigger: a
    plausible answer is returned as-is either way."""
    return generate_json_result(client, contents, model, prefer).text


def generate_json_result(client: genai.Client, contents: str, model: str | None = None,
                         prefer: str = "gemini") -> "GenerationResult":
    """generate_json with per-call provenance. The provider travels with the result, so
    concurrent requests can never attribute one call's fallback answer to another call."""
    if prefer == "deepseek":
        return GenerationResult(text=deepseek_generate_json(contents), provider="deepseek")
    try:
        return GenerationResult(text=_gemini_generate_json(client, contents, model),
                                provider="gemini")
    except errors.ClientError as exc:
        # Only a quota/rate-limit failure is transient for the fallback; a bad request
        # or an auth problem would fail (or mask a misconfiguration) on any provider.
        if exc.code != 429 or not config.DEEPSEEK_API_KEY:
            raise
    except (errors.ServerError, *NETWORK_ERRORS):
        if not config.DEEPSEEK_API_KEY:
            raise
    logger.warning("Gemini failed transiently after retries; falling back to DeepSeek (%s)",
                   config.DEEPSEEK_MODEL)
    return GenerationResult(text=deepseek_generate_json(contents), provider="deepseek")


def _gemini_generate_json(client: genai.Client, contents: str, model: str | None = None) -> str:
    """One Gemini JSON call, retried with back-off when the model is busy (503), rate-limited (429),
    or the connection fails (up to 2 retries). `model` defaults to GEMINI_MODEL (passport build)."""
    busy_retries = network_retries = 0
    while True:
        try:
            response = client.models.generate_content(
                model=model or GEMINI_MODEL,
                contents=contents,
                # temperature=0: a re-check should not change claims or levels by chance.
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
            )
            return response.text or ""
        except (errors.ServerError, errors.ClientError) as exc:
            temporary = isinstance(exc, errors.ServerError) or exc.code == 429
            if not temporary or busy_retries >= len(RETRY_DELAYS_SECONDS):
                raise
            delay = RETRY_DELAYS_SECONDS[busy_retries]
            busy_retries += 1
            logger.warning("Gemini busy (%s), retry %d in %ss", exc.code, busy_retries, delay)
        except NETWORK_ERRORS as exc:
            if network_retries >= len(NETWORK_RETRY_DELAYS_SECONDS):
                raise
            delay = NETWORK_RETRY_DELAYS_SECONDS[network_retries]
            network_retries += 1
            logger.warning("Gemini connection error (%s), retry %d in %ss", exc.__class__.__name__, network_retries, delay)
        time.sleep(delay)


@dataclasses.dataclass(frozen=True)
class GenerationResult:
    text: str
    provider: str  # "gemini" or "deepseek" - per-call provenance for cache decisions


DEEPSEEK_RETRY_DELAYS_SECONDS = (3, 8)


def _deepseek_request(contents: str) -> str:
    """One DeepSeek chat completion in JSON mode (OpenAI-compatible API). Always uses
    the configured DeepSeek model - callers pass Gemini model names, which must never
    be forwarded (DeepSeek rejects them with Model Not Exist)."""
    response = httpx.post(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"},
        json={
            "model": config.DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": contents}],
            "response_format": {"type": "json_object"},
            # Extraction calls need the answer, not billed chain-of-thought latency.
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "stream": False,
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"] or ""


def deepseek_generate_json(contents: str) -> str:
    """DeepSeek JSON call with short back-off on rate limits, outages and network errors."""
    if not config.DEEPSEEK_API_KEY:
        raise GeminiUnavailableError("DeepSeek fallback is not configured (DEEPSEEK_API_KEY).")
    retries = 0
    while True:
        try:
            return _deepseek_request(contents)
        except httpx.HTTPStatusError as exc:
            temporary = exc.response.status_code == 429 or exc.response.status_code >= 500
            if not temporary or retries >= len(DEEPSEEK_RETRY_DELAYS_SECONDS):
                raise GeminiUnavailableError(
                    f"DeepSeek fallback failed (HTTP {exc.response.status_code}).") from exc
        except (httpx.TransportError, ConnectionError) as exc:
            if retries >= len(DEEPSEEK_RETRY_DELAYS_SECONDS):
                raise GeminiUnavailableError("DeepSeek fallback connection failed.") from exc
        delay = DEEPSEEK_RETRY_DELAYS_SECONDS[retries]
        retries += 1
        logger.warning("DeepSeek retry %d in %ss", retries, delay)
        time.sleep(delay)


def _json_list(text: str, key: str) -> list[Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    data = json.loads(cleaned)
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise ValueError(f'Expected a JSON object with a "{key}" array.')
    return data[key]


def propose_json_items(prompt: str, key: str, cache_prefix: str) -> list[Any]:
    """One Gemini call on GEMINI_JD_MODEL returning the `key` array of a JSON object, cached by
    model and prompt (job-description requirements and project-report claims).

    Items are untrusted proposals: callers validate every one deterministically. Retries once on
    invalid JSON (GeminiOutputError after that); GeminiUnavailableError when Gemini isn't
    configured or the call fails.
    """
    if not config.GEMINI_API_KEY:
        raise GeminiUnavailableError("GEMINI_API_KEY is not configured.")
    model = config.GEMINI_JD_MODEL
    cache_key = hashlib.sha256(f"{model}\0{prompt}".encode("utf-8")).hexdigest()
    cache_path = config.CACHE_DIR / f"{cache_prefix}-{cache_key}.json"
    if cache_path.exists():
        try:
            return _json_list(cache_path.read_text(encoding="utf-8"), key)
        except (UnicodeDecodeError, ValueError):
            cache_path.unlink(missing_ok=True)
    contents = prompt
    last_error: Exception | None = None
    for attempt in range(2):
        prefer = "deepseek" if attempt and config.DEEPSEEK_API_KEY else "gemini"
        try:
            result = generate_json_result(genai.Client(api_key=config.GEMINI_API_KEY), contents,
                                          model=model, prefer=prefer)
        except Exception as exc:
            raise GeminiUnavailableError(f"Generation call failed: {exc.__class__.__name__}") from exc
        try:
            items = _json_list(result.text, key)
        except ValueError as exc:
            last_error = exc
            if attempt == 0:
                contents = f"{prompt}\nYour previous output was invalid: {exc}. Return corrected JSON only."
            continue
        if result.provider == "gemini":
            write_json_cache(cache_path, {key: items})
        return items
    raise GeminiOutputError(f"Generation returned invalid JSON after one retry: {last_error}")
