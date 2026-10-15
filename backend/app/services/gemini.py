import logging
import time

from google import genai
from google.genai import errors, types

from app.config import GEMINI_MODEL

logger = logging.getLogger(__name__)
RETRY_DELAYS_SECONDS = (3, 8, 20)


def generate_json(client: genai.Client, contents: str) -> str:
    """One Gemini JSON call, retried with back-off when the model is busy (503) or rate-limited (429)."""
    for attempt, delay in enumerate((*RETRY_DELAYS_SECONDS, None), start=1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            return response.text or ""
        except (errors.ServerError, errors.ClientError) as exc:
            temporary = isinstance(exc, errors.ServerError) or exc.code == 429
            if not temporary or delay is None:
                raise
            logger.warning("Gemini busy (%s), retry %d in %ss", exc.code, attempt, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")
