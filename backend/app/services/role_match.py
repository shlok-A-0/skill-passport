from typing import Any

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
    passport_skills: list[dict[str, Any]], target_role: str | dict[str, Any]
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
            reason = "No verified code, dependency, or configuration evidence found."
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
    return {
        "role_id": role["id"],
        "role_name": role["name"],
        "met": met_count,
        "total": len(requirements),
        "requirements": requirements,
    }
