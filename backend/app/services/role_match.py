from typing import Any

from app.services.coverage import coverage_phrase
from app.services.framework import get_roles, skill_map
from app.services.verifier import LEVEL_ORDER


def _get_role(role: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(role, dict):
        selected = role
    else:
        selected = next(
            (
                item for item in get_roles()
                if role in {item["id"], item["name"]}
            ),
            None,
        )
    if not selected:
        raise ValueError(f"Unknown target role: {role}")
    return selected


def match_role(
    passport_skills: list[dict[str, Any]], target_role: str | dict[str, Any],
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    role = _get_role(target_role)
    student_skills = {item["skill_id"]: item for item in passport_skills}
    requirements = []
    met_count = 0

    for requirement in role["requirements"]:
        skill_id = requirement["skill_id"]
        student = student_skills.get(skill_id)
        level = student.get("level", "Claimed") if student else None
        has_evidence = bool(student and student.get("evidence") and level != "Claimed")
        minimum = requirement["min_level"]
        if not has_evidence:
            status = "missing"
            # "Not found" in a sample of files, not "lacks the skill".
            reason = f"No evidence found in the analysed files{coverage_phrase(coverage)}."
        elif LEVEL_ORDER[level] >= LEVEL_ORDER[minimum]:
            status = "met"
            met_count += 1
            reason = f"Requires {minimum}; found {level}."
        else:
            status = "partial"
            evidence_types = sorted(
                {
                    item.get("type", "evidence")
                    for item in student.get("evidence", [])
                }
            )
            source = ", ".join(evidence_types) if evidence_types else "evidence"
            reason = f"Requires {minimum}; found {level} ({source} evidence only)."
        requirements.append(
            {
                "skill_id": skill_id,
                "name": (skill_map().get(skill_id) or {}).get("name", skill_id),
                "importance": requirement["importance"],
                "status": status,
                "student_level": level,
                "min_level": minimum,
                "reason": reason,
            }
        )

    requirements.sort(
        key=lambda item: (
            item["importance"] != "must",
            item["status"] == "met",
        )
    )

    must_items = [r for r in requirements if r["importance"] == "must"]
    optional_items = [r for r in requirements if r["importance"] != "must"]

    mandatory_met = sum(1 for r in must_items if r["status"] == "met")
    mandatory_total = len(must_items)
    mandatory_gaps = mandatory_total - mandatory_met

    optional_met = sum(1 for r in optional_items if r["status"] == "met")
    optional_total = len(optional_items)
    optional_gaps = optional_total - optional_met

    if mandatory_gaps == 0:
        headline = f"All {mandatory_total} mandatory requirements met"
        if optional_total > 0:
            headline += f" ({optional_met}/{optional_total} optional met)"
    else:
        gap_suffix = "s" if mandatory_gaps != 1 else ""
        headline = f"{mandatory_met} of {mandatory_total} mandatory requirements met ({mandatory_gaps} mandatory gap{gap_suffix})"

    return {
        "role_id": role["id"],
        "role_name": role["name"],
        "met": met_count,
        "total": len(requirements),
        "headline": headline,
        "mandatory_met": mandatory_met,
        "mandatory_total": mandatory_total,
        "mandatory_gaps": mandatory_gaps,
        "optional_met": optional_met,
        "optional_total": optional_total,
        "optional_gaps": optional_gaps,
        "requirements": requirements,
    }
