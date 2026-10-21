"""Deterministic checks for AI proposals that quote a document (job descriptions, project reports).

The AI only proposes: an item is kept only when its skill id is in the framework and its quote is
found in the source text. Nothing here calls an LLM.
"""
from collections.abc import Callable
from typing import Any

from app.services.framework import get_skills


def normalise(text: str) -> str:
    """Lowercase and collapse whitespace, so line breaks and spacing don't affect quote checks."""
    return " ".join(str(text).lower().split())


def quote_in_text(quote: str, text: str) -> bool:
    """True if the normalised quote is a non-empty substring of the normalised text."""
    needle = normalise(quote)
    return bool(needle) and needle in normalise(text)


def valid_skill_ids() -> set[str]:
    return {skill["id"] for skill in get_skills()}


def validate_proposals(
    items: list[Any],
    source_text: str,
    limit: int,
    prefer: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
    sort_key: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep proposals with a known skill id and a quote found in `source_text`.

    One item per skill: a later duplicate replaces the kept one only when `prefer(new, kept)` is
    true (default: keep the first). The kept items are ordered by `sort_key` (if given) and capped
    at `limit`. Returns (kept, dropped); each dropped item has the skill id, quote and a reason.
    """
    known = valid_skill_ids()
    by_skill: dict[str, dict[str, Any]] = {}
    dropped: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            dropped.append({"skill_id": None, "quote": None, "reason": "Not a valid proposal."})
            continue
        skill_id = item.get("skill_id")
        quote = item.get("quote")
        quote_text = quote.strip() if isinstance(quote, str) else ""
        if not isinstance(skill_id, str) or skill_id not in known:
            dropped.append({
                "skill_id": skill_id if isinstance(skill_id, str) else None,
                "quote": quote_text or None,
                "reason": "Not a skill in the skill framework.",
            })
            continue
        if not quote_in_text(quote_text, source_text):
            dropped.append({
                "skill_id": skill_id,
                "quote": quote_text or None,
                "reason": "Quote not found in the text." if quote_text else "No quote given.",
            })
            continue
        candidate = {**item, "quote": quote_text}
        kept = by_skill.get(skill_id)
        if kept is None or (prefer is not None and prefer(candidate, kept)):
            by_skill[skill_id] = candidate
    ordered = list(by_skill.values())
    if sort_key is not None:
        ordered.sort(key=sort_key)
    for item in ordered[limit:]:
        dropped.append({
            "skill_id": item["skill_id"],
            "quote": item["quote"],
            "reason": f"Over the limit of {limit}.",
        })
    return ordered[:limit], dropped
