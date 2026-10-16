"""Reviewer-ready evidence packet per role requirement (recruiter view only).

Deterministic, no LLM calls. Each requirement answers four questions: what supports it,
how clearly that work traces to the candidate (attribution, a label, never a number), what
is uncertain, and what to ask next. Attribution is about traceability, not ability.
"""
import re
from typing import Any

from app.services.coverage import coverage_phrase
from app.services.framework import skill_map
from app.services.verifier import EARLIER_SOURCE_KINDS, line_share

STATUS_LABELS = {"met": "Met", "partial": "Partial", "missing": "Not found"}
LIMITED_HISTORY = "Low (limited history)"
RANK = {"Low": 1, "Medium": 2, "High": 3}
# Warnings on a citation's repository or file that cap attribution at Medium, with the reason shown.
MEDIUM_CAPS = {
    "repo_history": None,  # worded from the commit count, see _cap_reason
    "bulk_upload": "Capped at Medium: a cited file was added in one large upload and not edited since.",
    "commit_spread": "Capped at Medium: all of the candidate's commits in this repository were made on one day.",
    "history_import": "Capped at Medium: commits are dated before this repository was created on GitHub.",
    "ai_leftover": "Capped at Medium: a cited file contains text pasted from an AI chat.",
    "copy_match": "Capped at Medium: a cited line also appears in another public repository.",
}
EARLIER_SOURCE_CAP = "Capped at Low: other code cited for this skill matched an earlier public source."
LIMITED_HISTORY_REASON = (
    "Low (limited history): fewer than 4 commits in this repository and line authorship unknown."
)
# Advisory signals that weaken attribution, with the uncertainty line each one adds.
ATTRIBUTION_WARNINGS = {
    "line_share": "Advisory warning: few cited lines written by the candidate",
    "commit_share": "Advisory warning: file created or mostly changed by someone else",
    "bulk_upload": "Advisory warning: file added in one large upload and not edited since",
    "copy_match": "Advisory warning: a cited line also appears in another public repository",
}
ONLY_FROM = {
    "dependency": "Evidence only from a dependency file",
    "config": "Evidence only from a config file",
    "commit": "Evidence only from commit history",
}


def _skill_name(skill_id: str) -> str:
    return (skill_map().get(skill_id) or {}).get("name", skill_id)


def _warnings(item: dict[str, Any]) -> list[str]:
    return [
        signal["id"] for signal in item.get("integrity") or []
        if signal.get("id") in ATTRIBUTION_WARNINGS and signal.get("status") == "warn"
    ]


def _base_attribution(code: list[dict[str, Any]]) -> str:
    """High: a code citation passes every hard check and the skill's code evidence has no
    line-share, file-creator, bulk-upload or copy warning. Medium: some of those warnings, but
    not on every citation. Low: dependency/config evidence only, or every code citation warned."""
    if not code:
        return "Low"
    warned = [item for item in code if _warnings(item)]
    if not warned and any(all((item.get("checks") or {}).values()) for item in code):
        return "High"
    if len(warned) == len(code):
        return "Low"
    return "Medium"


def _signal(item: dict[str, Any], signal_id: str) -> dict[str, Any] | None:
    return next((s for s in item.get("integrity") or [] if s.get("id") == signal_id), None)


def _repo_commits(signal: dict[str, Any]) -> int | None:
    if isinstance(signal.get("total"), int):
        return signal["total"]
    match = re.match(r"^(\d+) commits in the repository", signal.get("detail", ""))  # older passports
    return int(match.group(1)) if match else None


def _cap_reason(signal: dict[str, Any]) -> str:
    if signal["id"] == "repo_history":
        total = _repo_commits(signal)
        if total is None:
            return "Capped at Medium: fewer than 4 commits in this repository."
        return f"Capped at Medium: only {total} commit{'' if total == 1 else 's'} in this repository."
    return MEDIUM_CAPS[signal["id"]]


def _medium_caps(code: list[dict[str, Any]]) -> list[str]:
    """Reasons from warnings on the cited files or their repositories (each listed once)."""
    reasons: list[str] = []
    for item in code:
        for signal in item.get("integrity") or []:
            if signal.get("id") not in MEDIUM_CAPS or signal.get("status") != "warn" or signal.get("earlier_source"):
                continue
            reason = _cap_reason(signal)
            if reason not in reasons:
                reasons.append(reason)
    return reasons


def _limited_history(code: list[dict[str, Any]]) -> bool:
    """Every code citation sits in a repo with fewer than 4 commits and has unknown line authorship."""
    return bool(code) and all(
        (_signal(item, "repo_history") or {}).get("status") == "warn" and line_share(item) is None
        for item in code
    )


def attribution_details(
    status: str, evidence: list[dict[str, Any]], earlier_source: bool = False
) -> tuple[str, list[str]]:
    """The attribution label and the reasons for any cap that lowered it.

    Caps apply after the base rules: a warning on a cited file or its repository (thin history,
    bulk upload, one-day history, backdated history, AI chat text, a one-line copy match) caps at
    Medium; an earlier-source match on the skill caps at Low; fewer than 4 repo commits with
    unknown line authorship gives "Low (limited history)", never High.
    """
    if status == "missing" or not evidence:
        return "Not applicable", []
    code = [item for item in evidence if item.get("type") == "code"]
    if _limited_history(code):
        return LIMITED_HISTORY, [LIMITED_HISTORY_REASON]
    label = _base_attribution(code)
    medium_reasons = _medium_caps(code)
    if medium_reasons and RANK[label] > RANK["Medium"]:
        label = "Medium"
    if earlier_source:
        label = "Low"
        return label, [EARLIER_SOURCE_CAP]
    # When the base rules already gave Low, the advisory warnings explain it instead.
    return label, medium_reasons if label == "Medium" else []


def attribution(status: str, evidence: list[dict[str, Any]], earlier_source: bool = False) -> str:
    """High / Medium / Low / "Low (limited history)" / Not applicable; never a number."""
    return attribution_details(status, evidence, earlier_source)[0]


def _support(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Up to two evidence links, code first."""
    ordered = sorted(evidence, key=lambda item: item.get("type") != "code")
    return [
        {field: item.get(field) for field in ("type", "repo", "file_path", "start_line", "end_line", "url")}
        for item in ordered[:2]
    ]


def _uncertain(
    requirement: dict[str, Any], evidence: list[dict[str, Any]], coverage: dict[str, Any] | None,
    cap_reasons: list[str] | None = None,
) -> list[str]:
    if requirement["status"] == "missing":
        return [f"Not found in the analysed files{coverage_phrase(coverage)}"]
    lines = []
    if requirement["status"] == "partial":
        lines.append(f"Requires {requirement['min_level']}, found {requirement.get('student_level')}")
    if len(evidence) == 1:
        lines.append("Only one citation")
    types = {item.get("type") for item in evidence}
    if "code" not in types:
        for evidence_type, text in ONLY_FROM.items():
            if types == {evidence_type}:
                lines.append(text)
        if len(types) > 1:
            lines.append("Evidence only from dependency or config files")
    seen = []
    for item in evidence:
        for signal_id in _warnings(item):
            # A warning that capped attribution is listed once, as the cap's reason.
            if signal_id not in seen and MEDIUM_CAPS.get(signal_id) not in (cap_reasons or []):
                seen.append(signal_id)
    lines += [ATTRIBUTION_WARNINGS[signal_id] for signal_id in seen]
    return lines + (cap_reasons or [])


def _best_code(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    code = [item for item in evidence if item.get("type") == "code"]
    return max(code, key=lambda item: (not _warnings(item), item["end_line"] - item["start_line"]), default=None)


def ask_next(
    requirement: dict[str, Any], skill: dict[str, Any] | None, interview_kit: list[dict[str, Any]]
) -> dict[str, Any]:
    """The interview-kit question for the skill if there is one; otherwise a template."""
    skill_id = requirement["skill_id"]
    name = _skill_name(skill_id)
    entry = next((item for item in interview_kit if item.get("skill_id") == skill_id and item.get("questions")), None)
    if entry:
        question = entry["questions"][0]
        return {"question": question.get("q", ""), "good_answer_mentions": question.get("good_answer_mentions"),
                "source": "interview_kit"}
    evidence = (skill or {}).get("evidence") or []
    level = (skill or {}).get("level")
    if requirement["status"] == "missing" or not evidence or level == "Claimed":
        return {"question": f"Walk me through a project where you used {name}.", "source": "template"}
    code = _best_code(evidence)
    if code is None:  # Used only
        repo = evidence[0].get("repo", "your repository")
        return {"question": f"Where in {repo} do you use {name} beyond listing it as a dependency?",
                "source": "template"}
    where = f"{code['repo']}/{code['file_path']} (lines {code['start_line']}–{code['end_line']})"
    return {"question": f"In {where}, why did you implement this part the way you did?", "source": "template"}


def _summary(status: str, evidence: list[dict[str, Any]], label: str) -> str:
    if status == "missing":
        return "Not found in the analysed files"
    code = sum(1 for item in evidence if item.get("type") == "code")
    other = len(evidence) - code
    parts = []
    if code:
        parts.append(f"{code} code citation{'s' if code != 1 else ''}")
    if other:
        parts.append(f"{other} dependency/config item{'s' if other != 1 else ''}")
    return f"{', '.join(parts) or 'no counted evidence'} · attribution {label}"


def requirement_packets(passport: dict[str, Any]) -> list[dict[str, Any]]:
    skills = {item["skill_id"]: item for item in passport.get("skills", [])}
    kit = passport.get("interview_kit") or []
    coverage = passport.get("coverage")
    earlier_source_skills = {
        (item.get("claim") or {}).get("skill_id")
        for item in passport.get("rejected_claims") or []
        if item.get("kind") in EARLIER_SOURCE_KINDS
    }
    packets = []
    for requirement in (passport.get("role_match") or {}).get("requirements", []):
        skill = skills.get(requirement["skill_id"])
        status = requirement["status"]
        evidence = ((skill or {}).get("evidence") or []) if status != "missing" else []
        label, cap_reasons = attribution_details(
            status, evidence, earlier_source=requirement["skill_id"] in earlier_source_skills,
        )
        packets.append({
            "skill_id": requirement["skill_id"],
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "support": _support(evidence),
            "attribution": label,
            "uncertain": _uncertain(requirement, evidence, coverage, cap_reasons),
            "ask_next": ask_next(requirement, skill, kit),
            "summary": _summary(status, evidence, label),
        })
    return packets
