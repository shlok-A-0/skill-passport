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


def generate_json(client: genai.Client, contents: str) -> str:
    """One Gemini JSON call, retried with back-off when the model is busy (503), rate-limited (429),
    or the connection fails (up to 2 retries)."""
    busy_retries = network_retries = 0
    while True:
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
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


def _json_list(text: str, key: str) -> list[Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    data = json.loads(cleaned)
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise ValueError(f'Expected a JSON object with a "{key}" array.')
    return data[key]


def propose_json_items(prompt: str, key: str, cache_prefix: str) -> list[Any]:
    """One Gemini call returning the `key` array of a JSON object, cached by prompt.

    Items are untrusted proposals: callers validate every one deterministically. Retries once on
    invalid JSON (GeminiOutputError after that); GeminiUnavailableError when Gemini isn't
    configured or the call fails.
    """
    if not config.GEMINI_API_KEY:
        raise GeminiUnavailableError("GEMINI_API_KEY is not configured.")
    cache_key = hashlib.sha256(f"{GEMINI_MODEL}\0{prompt}".encode("utf-8")).hexdigest()
    cache_path = config.CACHE_DIR / f"{cache_prefix}-{cache_key}.json"
    if cache_path.exists():
        try:
            return _json_list(cache_path.read_text(encoding="utf-8"), key)
        except (UnicodeDecodeError, ValueError):
            cache_path.unlink(missing_ok=True)
    contents = prompt
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            text = generate_json(genai.Client(api_key=config.GEMINI_API_KEY), contents)
        except Exception as exc:
            raise GeminiUnavailableError(f"Gemini call failed: {exc.__class__.__name__}") from exc
        try:
            items = _json_list(text, key)
        except ValueError as exc:
            last_error = exc
            if attempt == 0:
                contents = f"{prompt}\nYour previous output was invalid: {exc}. Return corrected JSON only."
            continue
        write_json_cache(cache_path, {key: items})
        return items
    raise GeminiOutputError(f"Gemini returned invalid JSON after one retry: {last_error}")
