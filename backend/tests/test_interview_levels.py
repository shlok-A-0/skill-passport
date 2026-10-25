"""Interview-adjusted levels: the rule, its limits, and where it shows (recruiter views only)."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.interview_levels import adjusted_level, apply_interview_levels, skill_outcomes


def _row(skill_id: str, outcome: str, session: str = "s2", at: str = "2026-10-25T10:00:00+00:00") -> dict:
    return {"skill_id": skill_id, "outcome": outcome, "session_id": session, "assessed_at": at, "assessor_id": "alice"}


def _passport() -> dict:
    code = {"type": "code", "repo": "api", "file_path": "a.py", "start_line": 1, "end_line": 2}
    return {
        "id": "p1",
        "skills": [
            {"skill_id": "flask", "level": "Applied", "evidence": [code]},
            {"skill_id": "python", "level": "Demonstrated", "evidence": [code]},
            {"skill_id": "unit-testing", "level": "Applied", "evidence": [code]},
            {"skill_id": "docker", "level": "Used", "evidence": [{"type": "config"}]},
            {"skill_id": "aws", "level": "Claimed", "evidence": []},
        ],
        "role_match": {"role_id": "backend-intern", "met": 0, "total": 0, "requirements": []},
    }


def test_the_newest_set_decides_and_the_worst_answer_counts() -> None:
    rows = [  # newest set first, as storage returns them
        _row("flask", "explained_well", "s2"), _row("flask", "partly_explained", "s2"),
        _row("flask", "could_not_explain", "s1"),  # older set: superseded for flask
        _row("python", "explained_well", "s1"),    # only assessed in the older set: still counts
    ]
    outcomes = skill_outcomes(rows)
    assert outcomes["flask"]["outcome"] == "partly_explained" and outcomes["flask"]["questions"] == 2
    assert outcomes["python"]["outcome"] == "explained_well" and outcomes["python"]["session_id"] == "s1"


def test_steps_and_limits() -> None:
    assert adjusted_level("flask", "Applied", 1, True) == "Demonstrated"
    assert adjusted_level("flask", "Applied", -1, True) == "Used"
    assert adjusted_level("flask", "Applied", -2, True) == "Claimed"
    assert adjusted_level("flask", "Used", -2, True) == "Claimed"           # never below Claimed
    assert adjusted_level("python", "Demonstrated", 1, True) == "Demonstrated"  # never above Demonstrated
    assert adjusted_level("unit-testing", "Applied", 1, True) == "Applied"  # its reachable maximum
    assert adjusted_level("aws", "Claimed", 1, False) == "Claimed"          # no evidence: never above Claimed


def test_apply_moves_levels_and_keeps_the_evidence_level() -> None:
    passport = _passport()
    original = copy.deepcopy(passport)
    adjusted = apply_interview_levels(passport, [
        _row("flask", "could_not_explain"), _row("python", "partly_explained"),
        _row("docker", "explained_well"), _row("kubernetes", "explained_well"), _row("redis", "could_not_explain"),
    ])
    assert passport == original  # the input (stored passport) is never modified
    skills = {s["skill_id"]: s for s in adjusted["skills"]}
    assert (skills["flask"]["evidence_level"], skills["flask"]["level"]) == ("Applied", "Claimed")
    assert (skills["python"]["evidence_level"], skills["python"]["level"]) == ("Demonstrated", "Applied")
    assert (skills["docker"]["evidence_level"], skills["docker"]["level"]) == ("Used", "Applied")
    assert skills["flask"]["interview"]["outcome_label"] == "Couldn't explain"
    # Explained well on a skill with nothing found: a claim, never evidence. A poor answer adds nothing.
    assert skills["kubernetes"]["level"] == "Claimed" and skills["kubernetes"]["evidence"] == []
    assert "redis" not in skills
    assert {item["skill_id"] for item in adjusted["interview_adjustments"]} == {"flask", "python", "docker", "kubernetes", "redis"}
    assert adjusted["evidence_role_match"] == original["role_match"]


def test_role_match_follows_the_adjusted_levels() -> None:
    passport = _passport()
    passport["role_match"]["role_id"] = "backend-intern"
    from app.services.role_match import match_role
    passport["role_match"] = match_role(passport["skills"], "backend-intern")
    before = {r["skill_id"]: r["status"] for r in passport["role_match"]["requirements"]}
    assert before.get("python") == "met"
    adjusted = apply_interview_levels(passport, [_row("python", "could_not_explain")])
    after = {r["skill_id"]: r["status"] for r in adjusted["role_match"]["requirements"]}
    assert after["python"] != "met"
    assert adjusted["role_match"]["met"] == passport["role_match"]["met"] - 1


def test_a_demotion_to_claimed_keeps_the_verified_citations_visible() -> None:
    from app.services.packets import requirement_packets
    from app.services.role_match import match_role
    passport = _passport()
    passport["skills"].append({"skill_id": "sql", "level": "Applied", "evidence": [
        {"type": "code", "repo": "api", "file_path": "db.py", "start_line": 3, "end_line": 4, "url": "https://x/db.py#L3-L4",
         "checks": {"file_exists": True}}]})
    passport["role_match"] = match_role(passport["skills"], "backend-intern")
    adjusted = apply_interview_levels(passport, [_row("sql", "could_not_explain")])
    sql = next(r for r in adjusted["role_match"]["requirements"] if r["skill_id"] == "sql")
    assert sql["student_level"] == "Claimed" and sql["status"] == "partial"  # not "No evidence found"
    assert "Interview: Couldn't explain moved it from Applied to Claimed." in sql["reason"]
    packet = next(p for p in requirement_packets(adjusted) if p["skill_id"] == "sql")
    assert packet["support"] and packet["support"][0]["file_path"] == "db.py"


def test_no_outcomes_returns_the_passport_unchanged() -> None:
    passport = _passport()
    assert apply_interview_levels(passport, []) is passport
