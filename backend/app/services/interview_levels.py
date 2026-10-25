"""Interview-adjusted levels (owner decision, 2026-10-25).

A recruiter's recorded interview outcomes move a skill's level on the recruiter's view of the
passport: Explained well +1, Partly explained -1, Couldn't explain -2. A skill takes its worst
outcome in the most recent ready interview set that assessed it. Levels stay between Claimed and
Demonstrated (Unit Testing: Applied), and a skill with no counted evidence never rises above
Claimed: an interview can't create evidence, and role matching never counts a skill without it.

Deterministic, no LLM. Computed on read from the saved outcomes for recruiter responses only:
the stored passport, the student view and the badge keep the evidence level from the code checks,
which each adjusted skill also carries as `evidence_level`. Changing an outcome or a Re-check
therefore never leaves a stale level behind.
"""
import copy
from typing import Any

from app.services.framework import skill_map
from app.services.role_match import match_role
from app.services.verifier import LEVEL_ORDER, MAX_REACHABLE_LEVEL

OUTCOME_STEPS = {"explained_well": 1, "partly_explained": -1, "could_not_explain": -2}
OUTCOME_LABELS = {
    "explained_well": "Explained well",
    "partly_explained": "Partly explained",
    "could_not_explain": "Couldn't explain",
}
LEVELS = sorted(LEVEL_ORDER, key=LEVEL_ORDER.get)
NOT_FOUND = "Not found"


def skill_outcomes(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per skill: the worst outcome in the newest ready set that assessed it.

    `rows` come newest set first (storage.list_interview_outcomes); an older set only counts for
    skills no newer set assessed.
    """
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("outcome") not in OUTCOME_STEPS:
            continue
        current = result.get(row["skill_id"])
        if current is None:
            result[row["skill_id"]] = {
                "outcome": row["outcome"],
                "session_id": row["session_id"],
                "assessed_at": row["assessed_at"],
                "assessor_id": row["assessor_id"],
                "questions": 1,
            }
            continue
        if current["session_id"] != row["session_id"]:
            continue  # an older set: the newest set that assessed this skill decides
        current["questions"] += 1
        current["assessed_at"] = max(current["assessed_at"], row["assessed_at"])
        if OUTCOME_STEPS[row["outcome"]] < OUTCOME_STEPS[current["outcome"]]:
            current["outcome"] = row["outcome"]
            current["assessor_id"] = row["assessor_id"]
    return result


def _has_counted_evidence(skill: dict[str, Any] | None) -> bool:
    return bool(skill and skill.get("evidence") and skill.get("level") != "Claimed")


def adjusted_level(skill_id: str, evidence_level: str, steps: int, has_evidence: bool) -> str:
    """Raise up to the skill's reachable level (Claimed without counted evidence); drop to Claimed at most."""
    current = LEVEL_ORDER[evidence_level]
    if steps > 0:
        ceiling = LEVEL_ORDER[MAX_REACHABLE_LEVEL.get(skill_id, LEVELS[-1])] if has_evidence else LEVEL_ORDER["Claimed"]
        return LEVELS[max(current, min(current + steps, ceiling))]
    return LEVELS[max(0, current + steps)]


def apply_interview_levels(passport: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A copy of the passport with interview-adjusted levels and role match (input unchanged)."""
    outcomes = skill_outcomes(rows)
    if not outcomes:
        return passport
    adjusted = copy.deepcopy(passport)
    skills = {item["skill_id"]: item for item in adjusted.get("skills", [])}
    changes = []
    for skill_id, outcome in sorted(outcomes.items()):
        steps = OUTCOME_STEPS[outcome["outcome"]]
        skill = skills.get(skill_id)
        name = (skill_map().get(skill_id) or {}).get("name", skill_id)
        evidence_level = skill["level"] if skill else NOT_FOUND
        if skill is None and steps > 0:
            # Explained well in a discussion, but nothing was found: it becomes a claim, never evidence.
            skill = {"skill_id": skill_id, "name": name, "level": "Claimed", "certified": False, "evidence": []}
            adjusted.setdefault("skills", []).append(skill)
            skills[skill_id] = skill
            new_level = "Claimed"
        elif skill is None:
            new_level = NOT_FOUND
        else:
            new_level = adjusted_level(skill_id, evidence_level, steps, _has_counted_evidence(skill))
        record = {
            "outcome": outcome["outcome"],
            "outcome_label": OUTCOME_LABELS[outcome["outcome"]],
            "steps": steps,
            "evidence_level": evidence_level,
            "level": new_level,
            "session_id": outcome["session_id"],
            "assessed_at": outcome["assessed_at"],
            "assessor_id": outcome["assessor_id"],
            "questions": outcome["questions"],
        }
        if skill is not None:
            skill["evidence_level"] = evidence_level
            skill["level"] = new_level
            skill["interview"] = record
        changes.append({"skill_id": skill_id, "name": name, **record})

    adjusted["evidence_role_match"] = passport.get("role_match")
    role = adjusted.get("custom_role") or (adjusted.get("role_match") or {}).get("role_id")
    if role:
        try:
            adjusted["role_match"] = match_role(adjusted["skills"], role, adjusted.get("coverage"))
        except ValueError:
            pass  # a role that no longer exists keeps its stored match
    adjusted["interview_adjustments"] = changes
    return adjusted
