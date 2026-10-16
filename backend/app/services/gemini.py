import logging
import time

import httpx
from google import genai
from google.genai import errors, types

from app.config import GEMINI_MODEL

logger = logging.getLogger(__name__)
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
