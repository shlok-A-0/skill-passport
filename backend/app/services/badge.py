from typing import Any

from app.services.framework import skill_map
from app.services.verifier import LEVEL_DESCRIPTIONS, LEVEL_NOTE


def create_badge(passport: dict[str, Any]) -> dict[str, Any]:
    achievements = []
    for skill in passport["skills"]:
        if skill["level"] == "Claimed" or not skill["evidence"]:
            continue
        # Readable name ("REST API design"); older passports have no name, so look it up.
        name = skill.get("name") or (skill_map().get(skill["skill_id"]) or {}).get("name") or skill["skill_id"]
        evidence_items = []
        for item in skill["evidence"]:
            entry = {
                "type": "Evidence",
                "id": item["url"],
                "narrative": (
                    f"{item['type']} evidence for {name}; level {skill['level']}. "
                    f"Verification checks: {item['checks']}."
                ),
            }
            if item.get("record"):
                # Commit, exact lines, snippet hash, retrieval time, verifier version and rules.
                entry["evidenceRecord"] = item["record"]
            evidence_items.append(entry)
        achievements.append(
            {
                "type": "Achievement",
                "name": name,
                "skillId": skill["skill_id"],
                "description": (
                    f"{skill['level']}: {LEVEL_DESCRIPTIONS[skill['level']]} {LEVEL_NOTE} "
                    "This is an unsigned prototype."
                ),
                "criteria": {
                    "narrative": (
                        "Skill level is determined by deterministic evidence checks; "
                        "the credential is unsigned."
                    )
                },
                "evidence": evidence_items,
                "level": skill["level"],
            }
        )
    return {
        "@context": [
            "https://www.w3.org/ns/credentials/v2",
            "https://purl.imsglobal.org/spec/ob/v3p0/context.json",
        ],
        "type": ["VerifiableCredential", "OpenBadgeCredential"],
        "name": "Skill Passport (unsigned prototype)",
        "description": "Evidence-backed skills; this prototype is not cryptographically signed.",
        "issuer": {
            "type": "Profile",
            "name": "Skill Passport (prototype)",
            "url": "https://github.com/",
        },
        "credentialSubject": {
            "type": "AchievementSubject",
            "identifier": {
                "type": "IdentityObject",
                "identity": f"https://github.com/{passport['github_username']}",
                "identityType": "url",
            },
            "achievement": achievements,
        },
        "issuanceDate": passport["created_at"],
        "prototypeNote": "unsigned prototype",
    }
