import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.packets import ask_next, attribution, attribution_details, requirement_packets

ALL_CHECKS = {"file_exists": True, "lines_valid": True, "authored": True, "not_fork": True, "signature_found": True}


def code(path: str = "api/routes.py", warn: tuple[str, ...] = (), start: int = 1, end: int = 20) -> dict:
    return {
        "type": "code", "repo": "shop", "file_path": path, "start_line": start, "end_line": end,
        "url": f"https://github.com/s/shop/blob/x/{path}#L{start}-L{end}", "checks": dict(ALL_CHECKS),
        "integrity": [{"id": signal, "status": "warn"} for signal in warn]
        + [{"id": "repo_history", "status": "ok", "total": 12, "detail": "12 commits in the repository."}],
    }


def dependency() -> dict:
    return {"type": "dependency", "repo": "shop", "file_path": "requirements.txt", "start_line": 3,
            "end_line": 3, "url": "u", "checks": {"file_exists": True, "authored": True, "not_fork": True}}


def test_attribution_high_medium_low_and_not_applicable() -> None:
    assert attribution("met", [code(), dependency()]) == "High"
    # One clean citation, one with a warning.
    assert attribution("met", [code(), code("api/other.py", warn=("line_share",))]) == "Medium"
    # Every code citation has a warning (file creator / bulk upload / earlier-source match).
    assert attribution("met", [code(warn=("commit_share",)), code("b.py", warn=("bulk_upload", "copy_match"))]) == "Low"
    # Only dependency or config evidence.
    assert attribution("partial", [dependency()]) == "Low"
    assert attribution("missing", []) == "Not applicable"


def capped(signal_id: str, **fields: object) -> dict:
    """A clean citation (all checks, 20 of 20 lines written) with one warning on its file or repo."""
    item = code()
    item["integrity"] = [
        {"id": "line_share", "status": "ok", "written": 20, "cited": 20},
        {"id": "repo_history", "status": "ok", "total": 12},
    ]
    item["integrity"] = [s for s in item["integrity"] if s["id"] != signal_id]
    item["integrity"].append({"id": signal_id, "status": "warn", **fields})
    return item


def test_each_repo_or_file_warning_caps_attribution_at_medium() -> None:
    clean = capped("ai_leftover")
    clean["integrity"][-1]["status"] = "ok"
    assert attribution_details("met", [clean]) == ("High", [])
    expected = {
        "repo_history": "Capped at Medium: only 1 commit in this repository.",
        "bulk_upload": "Capped at Medium: a cited file was added in one large upload and not edited since.",
        "commit_spread": "Capped at Medium: all of the candidate's commits in this repository were made on one day.",
        "history_import": "Capped at Medium: commits are dated before this repository was created on GitHub.",
        "ai_leftover": "Capped at Medium: a cited file contains text pasted from an AI chat.",
        "copy_match": "Capped at Medium: a cited line also appears in another public repository.",
    }
    for signal_id, reason in expected.items():
        fields = {"total": 1} if signal_id == "repo_history" else {}
        # A clean citation plus one citation carrying the warning: never High.
        label, reasons = attribution_details("met", [clean, capped(signal_id, **fields), dependency()])
        assert (label, reasons) == ("Medium", [reason]), signal_id
        # Alone, a file-level warning that is also a base-rule warning already gives Low.
        assert attribution("met", [capped(signal_id, **fields)]) in {"Medium", "Low"}, signal_id
    # The count is read from older passports' detail text too.
    old = capped("repo_history", detail="2 commits in the repository, 2 by you.")
    assert attribution_details("met", [old])[1] == ["Capped at Medium: only 2 commits in this repository."]


def test_earlier_source_on_the_same_skill_caps_attribution_at_low() -> None:
    clean = capped("ai_leftover")
    clean["integrity"][-1]["status"] = "ok"
    assert attribution("met", [clean], earlier_source=True) == "Low"
    passport = _passport(
        [{"skill_id": "flask", "status": "met", "min_level": "Applied", "student_level": "Applied"}],
        [{"skill_id": "flask", "level": "Applied", "evidence": [clean]}],
    )
    passport["rejected_claims"] = [{"kind": "earlier_source", "claim": {"skill_id": "flask"}}]
    packet = requirement_packets(passport)[0]
    assert packet["attribution"] == "Low"
    assert "Capped at Low: other code cited for this skill matched an earlier public source." in packet["uncertain"]


def test_thin_history_with_unknown_line_authorship_is_low_limited_history() -> None:
    item = code()  # no line_share signal: authorship unknown
    item["integrity"] = [{"id": "repo_history", "status": "warn", "total": 1}]
    label, reasons = attribution_details("met", [item])
    assert label == "Low (limited history)"
    assert reasons[-1] == "Low (limited history): fewer than 4 commits in this repository and line authorship unknown."
    # Known line authorship in a thin repo: capped at Medium instead.
    assert attribution("met", [capped("repo_history", total=1)]) == "Medium"


def test_capped_warning_is_listed_once_in_whats_uncertain() -> None:
    passport = _passport(
        [{"skill_id": "flask", "status": "met", "min_level": "Applied", "student_level": "Applied"}],
        [{"skill_id": "flask", "level": "Applied", "evidence": [capped("bulk_upload"), capped("repo_history", total=1)]}],
    )
    packet = requirement_packets(passport)[0]
    assert packet["attribution"] == "Medium"
    assert packet["uncertain"] == [
        "Capped at Medium: a cited file was added in one large upload and not edited since.",
        "Capped at Medium: only 1 commit in this repository.",
    ]


def _passport(requirements: list[dict], skills: list[dict], kit: list[dict] | None = None) -> dict:
    return {
        "role_match": {"requirements": requirements},
        "skills": skills,
        "interview_kit": kit or [],
        "coverage": {"source_files_read": 28, "source_files_total": 168},
    }


def test_packets_answer_the_four_questions() -> None:
    passport = _passport(
        [
            {"skill_id": "flask", "status": "met", "min_level": "Applied", "student_level": "Applied"},
            {"skill_id": "docker", "status": "partial", "min_level": "Applied", "student_level": "Used"},
            {"skill_id": "aws", "status": "missing", "min_level": "Used", "student_level": "Claimed"},
        ],
        [
            {"skill_id": "flask", "level": "Applied", "evidence": [dependency(), code(warn=("line_share",))]},
            {"skill_id": "docker", "level": "Used", "evidence": [{**dependency(), "type": "config", "file_path": "Dockerfile"}]},
            {"skill_id": "aws", "level": "Claimed", "evidence": []},
        ],
        [{"skill_id": "flask", "questions": [{"q": "Why does routes.py retry twice?", "good_answer_mentions": "idempotency"}]}],
    )
    flask, docker, aws = requirement_packets(passport)
    assert flask["status_label"] == "Met" and flask["attribution"] == "Low"
    assert flask["support"][0]["type"] == "code" and len(flask["support"]) == 2  # code first, max 2
    assert "Advisory warning: few cited lines written by the candidate" in flask["uncertain"]
    assert flask["ask_next"] == {"question": "Why does routes.py retry twice?", "good_answer_mentions": "idempotency",
                                 "source": "interview_kit"}
    assert docker["status_label"] == "Partial" and docker["attribution"] == "Low"
    assert docker["uncertain"] == ["Requires Applied, found Used", "Only one citation", "Evidence only from a config file"]
    assert aws["status_label"] == "Not found" and aws["attribution"] == "Not applicable"
    assert aws["support"] == []
    assert aws["uncertain"] == ["Not found in the analysed files (28 of 168 source files read)"]
    assert aws["summary"] == "Not found in the analysed files"
    assert all(not any(char.isdigit() for char in packet["attribution"]) for packet in (flask, docker, aws))


def test_ask_next_templates_when_no_interview_question() -> None:
    used = ask_next({"skill_id": "docker", "status": "partial"}, {"level": "Used", "evidence": [dependency()]}, [])
    assert used == {"question": "Where in shop do you use Docker beyond listing it as a dependency?", "source": "template"}
    missing = ask_next({"skill_id": "aws", "status": "missing"}, None, [])
    assert missing == {"question": "Walk me through a project where you used AWS.", "source": "template"}
    applied = ask_next(
        {"skill_id": "flask", "status": "met"},
        {"level": "Applied", "evidence": [code("api/short.py", end=3), code("api/routes.py", end=40)]},
        [{"skill_id": "react", "questions": [{"q": "Not this one", "good_answer_mentions": "x"}]}],
    )
    assert applied == {"question": "In shop/api/routes.py (lines 1–40), why did you implement this part the way you did?",
                       "source": "template"}


def test_requirement_without_interview_question_gets_a_template() -> None:
    passport = _passport(
        [{"skill_id": "pandas", "status": "met", "min_level": "Applied", "student_level": "Demonstrated"}],
        [{"skill_id": "pandas", "level": "Demonstrated", "evidence": [code("billing/engine.py")]}],
        [{"skill_id": "flask", "questions": [{"q": "Flask only", "good_answer_mentions": "x"}]}],
    )
    packet = requirement_packets(passport)[0]
    assert packet["ask_next"]["source"] == "template"
    assert packet["ask_next"]["question"].startswith("In shop/billing/engine.py")
    assert packet["attribution"] == "High"
    assert packet["uncertain"] == ["Only one citation"]
