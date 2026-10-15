import re
import json
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from app.services.framework import skill_map
from app.services.github_ingest import CI_DIRS, CI_FILES, DOCKER_FILES, is_test_path

LEVEL_ORDER = {"Claimed": 0, "Used": 1, "Applied": 2, "Demonstrated": 3}
# Integrity signals are advisory: they are shown next to the evidence but never change
# verification or ladder level (a bulk upload is common for honest students too).
MIN_LINE_SHARE = 0.5
MIN_COMMIT_SHARE = 0.5
BULK_COMMIT_FILES = 20
BULK_COMMIT_ADDITIONS = 2000
MIN_REPO_COMMITS = 4
# Text an AI chat assistant writes around code, which ends up in a file when the answer is
# pasted wholesale. Finding one is concrete; not finding one does not mean a human wrote it.
AI_CHAT_LEFTOVERS = re.compile(
    r"as an ai (language )?model|here'?s (the|your|an?) (updated|complete|full|revised|modified|corrected|improved) "
    r"(code|version|file|script)|here is (the|your) (updated|complete|full|revised|modified) (code|version|file)"
    r"|i hope this helps|let me know if you (need|have|want)|certainly! here|sure! here"
    r"|^\s*```|(rest of (the|your) (code|implementation) (remains|stays|goes) (the same|unchanged|here))"
    r"|existing code (remains|stays) (the same|unchanged)",
    re.I,
)
COMMENT_OR_FENCE = re.compile(r"^\s*(#|//|/\*|\*|--|<!--|```)")


def _signal(signal_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": signal_id, "label": label, "status": status, "detail": detail}


def _same_user(login: str | None, username: str) -> bool:
    return bool(login) and login.lower() == username.lower()


def _line_share_signal(
    source: dict[str, Any], start: int, end: int, username: str
) -> dict[str, str]:
    label = "Cited lines written by you"
    blame = source.get("blame")
    if not blame:
        return _signal("line_share", label, "unknown", "Line history (git blame) was not available.")
    cited = end - start + 1
    yours = sum(
        max(0, min(end, range_end) - max(start, range_start) + 1)
        for range_start, range_end, login in blame
        if _same_user(login, username)
    )
    share = yours / cited
    status = "ok" if share >= MIN_LINE_SHARE else "warn"
    return _signal(
        "line_share", label, status,
        f"{yours} of {cited} cited lines were last changed in your commits.",
    )


def _commit_share_signal(source: dict[str, Any], username: str) -> dict[str, str]:
    label = "Your share of this file's commits"
    history = source.get("history")
    if not history or not history.get("total"):
        return _signal("commit_share", label, "unknown", "File commit history was not available.")
    total, by_user = history["total"], history["by_user"]
    more = "" if history.get("complete") else "+"
    detail = f"You made {by_user} of {total}{more} commits to this file"
    creator = history.get("creator_login")
    created_by_other = history.get("complete") and not _same_user(creator, username)
    if created_by_other:
        detail += f"; it was created by {creator or 'an unlinked git author'}"
    status = "warn" if created_by_other or by_user / total < MIN_COMMIT_SHARE else "ok"
    return _signal("commit_share", label, status, detail + ".")


def _bulk_upload_signal(source: dict[str, Any]) -> dict[str, str]:
    label = "Built up over several commits"
    history = source.get("history") or {}
    creation = history.get("creation")
    if not creation:
        return _signal("bulk_upload", label, "unknown", "The commit that created this file was not available.")
    files, additions = creation["files_changed"], creation["total_additions"]
    later = max(0, history.get("total", 1) - 1)
    bulk = files >= BULK_COMMIT_FILES or additions >= BULK_COMMIT_ADDITIONS
    detail = f"Created in a commit that changed {files} files (+{additions} lines); edited in {later} later commit(s)."
    return _signal("bulk_upload", label, "warn" if bulk and later == 0 else "ok", detail)


def _repo_history_signal(repo: dict[str, Any]) -> dict[str, str]:
    label = "Repository has a real commit history"
    counts = repo.get("commit_counts") or {}
    total, by_user = counts.get("total"), counts.get("by_user")
    if not total:
        return _signal("repo_history", label, "unknown", "Repository commit count was not available.")
    detail = f"{total} commits in the repository"
    if by_user is not None:
        detail += f", {by_user} by you"
    return _signal("repo_history", label, "warn" if total < MIN_REPO_COMMITS else "ok", detail + ".")


def _ai_leftover_signal(source: dict[str, Any]) -> dict[str, str]:
    label = "No AI chat text left in the file"
    for number, line in enumerate(source.get("lines", []), start=1):
        if COMMENT_OR_FENCE.match(line) and AI_CHAT_LEFTOVERS.search(line):
            text = line.strip()[:80]
            return _signal(
                "ai_leftover", label, "warn",
                f'Line {number} looks like pasted AI chat text: "{text}".',
            )
    return _signal(
        "ai_leftover", label, "ok",
        "No AI chat phrases found in the file (this cannot rule out AI-written code).",
    )


def integrity_signals(
    repo: dict[str, Any], source: dict[str, Any], start: int, end: int, username: str
) -> list[dict[str, str]]:
    return [
        _line_share_signal(source, start, end, username),
        _commit_share_signal(source, username),
        _bulk_upload_signal(source),
        _repo_history_signal(repo),
        _ai_leftover_signal(source),
    ]


def _safe_path(path: str) -> bool:
    return bool(path) and "\\" not in path and not PurePosixPath(path).is_absolute() and ".." not in PurePosixPath(path).parts


def _repo_by_name(repos: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next(
        (
            repo for repo in repos
            if name in {repo.get("name"), repo.get("full_name")}
        ),
        None,
    )


def _make_url(repo: dict[str, Any], commit_sha: str, path: str, start: int, end: int) -> str:
    escaped_path = quote(path, safe="/")
    full_name = repo.get("full_name", repo["name"])
    return f"https://github.com/{full_name}/blob/{commit_sha}/{escaped_path}#L{start}-L{end}"


def _signature_pattern(signature: str) -> re.Pattern[str]:
    escaped = re.escape(signature)
    if signature[:1].isalnum() or signature[:1] == "_":
        # "it(" must not match "split(", "app.get(" must not match "myapp.get(".
        escaped = r"(?<![A-Za-z0-9_])" + escaped
    return re.compile(escaped, re.IGNORECASE)


def _signature_found(skill: dict[str, Any], path: str, lines: list[str], start: int, end: int) -> bool:
    extension = PurePosixPath(path).suffix.lower()
    extensions = {item.lower() for item in skill.get("file_extensions", [])}
    if skill.get("category") == "Language":
        # A language skill is proven by the file's language, nothing else.
        return extension in extensions
    if extensions and extension not in extensions:
        return False
    # Frameworks, tools and practices need an actual signature near the cited lines.
    window = "\n".join(lines[max(0, start - 1 - 5): min(len(lines), end + 5)])
    return any(
        _signature_pattern(signature).search(window)
        for signature in skill.get("code_signatures", [])
    )


def contains_signature(text: str, signature: str) -> bool:
    """Case-insensitive; a signature starting with a letter must start a word ("it(" is not in "emit(")."""
    prefix = r"(?<!\w)" if signature[:1].isalnum() or signature[:1] == "_" else ""
    return re.search(prefix + re.escape(signature), text, flags=re.IGNORECASE) is not None


def _verify_claim(
    claim: dict[str, Any], repos: list[dict[str, Any]], username: str = ""
) -> dict[str, Any]:
    skill = skill_map().get(claim.get("skill_id", ""))
    repo = _repo_by_name(repos, claim.get("repo", ""))
    path = claim.get("file_path", "")
    safe_path = _safe_path(path) if isinstance(path, str) else False
    file_exists = bool(repo and safe_path and path in repo.get("tree", []))
    source = repo.get("source_files", {}).get(path) if file_exists else None
    lines = source.get("lines", []) if source else []
    start, end = claim.get("start_line", 0), claim.get("end_line", 0)
    lines_valid = bool(
        source
        and isinstance(start, int)
        and isinstance(end, int)
        and 1 <= start <= end <= len(lines)
    )
    authored_shas = source.get("authored_shas", []) if source else []
    authored = bool(authored_shas)
    not_fork = bool(repo and not repo.get("is_fork", False))
    signature_found = bool(
        skill and lines_valid and _signature_found(skill, path, lines, start, end)
    )
    checks = {
        "file_exists": file_exists,
        "lines_valid": lines_valid,
        "authored": authored,
        "not_fork": not_fork,
        "signature_found": signature_found,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    evidence = None
    if not failed_checks and skill and repo and source:
        commit_sha = repo.get("head_sha") or authored_shas[0]
        evidence = {
            "type": "code",
            "repo": repo["name"],
            "file_path": path,
            "start_line": start,
            "end_line": end,
            "commit_sha": commit_sha,
            "url": _make_url(repo, commit_sha, path, start, end),
            "snippet": "\n".join(lines[start - 1:end]),
            "explanation": claim.get("explanation", ""),
            "checks": checks,
            "integrity": integrity_signals(repo, source, start, end, username),
        }
    return {
        "verified": not failed_checks,
        "evidence": evidence,
        "checks": checks,
        "failed_checks": failed_checks,
    }


def _config_matches(configured: str, path: str) -> bool:
    """Directory entries match by prefix; bare file names match at any depth (backend/Dockerfile)."""
    if configured.endswith("/"):
        return path.startswith(configured)
    if "/" in configured:
        return path == configured
    return PurePosixPath(path).name == configured


def _is_ci_or_deploy(path: str) -> bool:
    return (
        PurePosixPath(path).name in DOCKER_FILES
        or path.startswith(CI_DIRS)
        or path in CI_FILES
        or path.startswith(("k8s/", "kubernetes/"))
    )


def _test_code_evidence(
    skill: dict[str, Any], repo: dict[str, Any], username: str
) -> list[dict[str, Any]]:
    """Cite a fetched, authored test file at the first line containing a test signature."""
    evidence = []
    for path, source in repo.get("source_files", {}).items():
        if not is_test_path(path) or not source.get("authored_shas"):
            continue
        lines = source.get("lines", [])
        first = next(
            (
                number for number, line in enumerate(lines, start=1)
                if any(contains_signature(line, sig) for sig in skill.get("code_signatures", []))
            ),
            None,
        )
        if first is None:
            continue
        start, end = first, min(len(lines), first + 9)
        sha = repo.get("head_sha") or source["authored_shas"][0]
        evidence.append({
            "type": "code",
            "repo": repo["name"],
            "file_path": path,
            "start_line": start,
            "end_line": end,
            "commit_sha": sha,
            "url": _make_url(repo, sha, path, start, end),
            "snippet": "\n".join(lines[start - 1:end]),
            "explanation": f"Test code in {path} written by you.",
            "checks": {
                "file_exists": path in repo.get("tree", []),
                "lines_valid": True,
                "authored": True,
                "not_fork": not repo.get("is_fork", False),
                "signature_found": True,
            },
            "integrity": integrity_signals(repo, source, start, end, username),
        })
    return evidence


def _commit_evidence(repo: dict[str, Any], username: str) -> dict[str, Any] | None:
    """Commits the user authored in a non-fork repo show they use Git."""
    if repo.get("is_fork"):
        return None
    sha = next(
        (
            source["authored_shas"][0]
            for source in repo.get("source_files", {}).values()
            if source.get("authored_shas")
        ),
        None,
    )
    by_user = (repo.get("commit_counts") or {}).get("by_user")
    if not sha:
        return None
    count = f"{by_user} commits" if by_user else "Commits"
    return {
        "type": "commit",
        "repo": repo["name"],
        "file_path": "",
        "start_line": 0,
        "end_line": 0,
        "commit_sha": sha,
        "url": f"https://github.com/{repo.get('full_name', repo['name'])}/commit/{sha}",
        "snippet": "",
        "explanation": f"{count} in {repo['name']} were authored by {username or 'you'}; link opens one of them.",
        "checks": {"authored": True, "not_fork": True},
    }


def _dependency_matches(skill: dict[str, Any], path: str, text: str) -> bool:
    return _dependency_match_line(skill, path, text) is not None


def _dependency_match_line(
    skill: dict[str, Any], path: str, text: str
) -> int | None:
    package_names = skill.get("dependency_names", [])
    basename = PurePosixPath(path).name.lower()
    declared_names: set[str] | None = None
    if basename == "package.json":
        try:
            package = json.loads(text)
            declared_names = set(package.get("dependencies", {})) | set(
                package.get("devDependencies", {})
            )
        except (ValueError, AttributeError):
            pass

    for line_number, line in enumerate(text.splitlines(), start=1):
        for name in package_names:
            if declared_names is not None and not any(
                declared.lower() == name.lower()
                or (name.startswith("@") and declared.lower().startswith(name.lower()))
                for declared in declared_names
            ):
                continue
            if re.search(
                rf"(?<![A-Za-z0-9_.-]){re.escape(name)}(?![A-Za-z0-9_.-])",
                line,
                re.I,
            ):
                return line_number
    return None


def _dependency_path_priority(path: str) -> tuple[int, str]:
    non_primary_folders = {
        "doc", "docs", "example", "examples", "fixture", "fixtures",
        "sample", "samples", "test", "tests", "test-data", "test-fixture",
        "test-fixtures", "testdata", "__fixtures__",
    }
    folders = {
        part.lower().replace("_", "-")
        for part in PurePosixPath(path).parts[:-1]
    }
    return int(bool(folders & non_primary_folders)), path.lower()


def _deterministic_evidence(
    skill: dict[str, Any], repos: list[dict[str, Any]], username: str = ""
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    dependency_matches = []
    for repo_index, repo in enumerate(repos):
        for path, content in repo.get("dependency_contents", {}).items():
            matched_line = _dependency_match_line(skill, path, content)
            if matched_line is not None:
                dependency_matches.append(
                    (
                        _dependency_path_priority(path),
                        repo_index,
                        path.lower(),
                        repo,
                        path,
                        content,
                        matched_line,
                    )
                )
    for _, _, _, repo, path, content, matched_line in sorted(
        dependency_matches, key=lambda match: match[:3]
    ):
        evidence.append(
            _dependency_evidence(repo, path, content, skill["name"], matched_line)
        )

    for repo in repos:
        for path, content in repo.get("config_contents", {}).items():
            configured = any(
                _config_matches(configured_path, path)
                for configured_path in skill.get("config_files", [])
            )
            if configured:
                evidence.append(_config_evidence(repo, path, content, "config", skill["name"]))
        if skill["id"] == "unit-testing" and repo.get("test_files"):
            evidence.extend(_test_code_evidence(skill, repo, username))
            # One grouped item per repo instead of one per test file.
            test_files = repo["test_files"]
            item = _config_evidence(repo, test_files[0], "", "config", skill["name"])
            others = f" and {len(test_files) - 1} more" if len(test_files) > 1 else ""
            item["explanation"] = f"{len(test_files)} test file(s) in {repo['name']}: {test_files[0]}{others}."
            evidence.append(item)
        if skill["id"] == "git":
            commit = _commit_evidence(repo, username)
            if commit:
                evidence.append(commit)
    return evidence


def _dependency_evidence(
    repo: dict[str, Any], path: str, content: str, skill_name: str, matched_line: int
) -> dict[str, Any]:
    lines = content.splitlines()
    context_start = max(1, matched_line - 2)
    context_end = min(len(lines), matched_line + 2)
    sha = repo.get("head_sha") or "HEAD"
    return {
        "type": "dependency",
        "repo": repo["name"],
        "file_path": path,
        "start_line": matched_line,
        "end_line": matched_line,
        "snippet_start_line": context_start,
        "matched_line": matched_line,
        "commit_sha": sha,
        "url": _make_url(repo, sha, path, matched_line, matched_line),
        "snippet": "\n".join(lines[context_start - 1:context_end]),
        "explanation": f"{skill_name} is listed by name in {path}.",
        "checks": {
            "file_exists": path in repo.get("tree", []),
            "lines_valid": matched_line <= len(lines),
            "authored": False,
            "not_fork": not repo.get("is_fork", False),
            "signature_found": True,
        },
    }


def _config_evidence(
    repo: dict[str, Any], path: str, content: str, evidence_type: str, skill_name: str
) -> dict[str, Any]:
    lines = content.splitlines()
    end = max(1, min(len(lines), 12))
    sha = repo.get("head_sha") or "HEAD"
    return {
        "type": evidence_type,
        "repo": repo["name"],
        "file_path": path,
        "start_line": 1,
        "end_line": end,
        "commit_sha": sha,
        "url": _make_url(repo, sha, path, 1, end),
        "snippet": "\n".join(lines[:end]),
        "explanation": f"{skill_name} is referenced by {path}.",
        "checks": {
            "file_exists": path in repo.get("tree", []),
            "lines_valid": bool(content) and end <= len(lines),
            "authored": False,
            "not_fork": not repo.get("is_fork", False),
            "signature_found": True,
        },
    }


def verify_claims(
    claims: list[dict[str, Any]],
    ingestion: dict[str, Any],
    resume_skills: list[dict[str, str]] | None = None,
    certificate_skills: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    repos = ingestion.get("repos", [])
    username = ingestion.get("username", "")
    resume_by_skill = {
        item["skill_id"]: item["mention"] for item in (resume_skills or [])
    }
    certified_ids = {item["skill_id"] for item in (certificate_skills or [])}
    evidence_by_skill: dict[str, list[dict[str, Any]]] = {}
    rejected_claims = []

    for claim in claims:
        result = _verify_claim(claim, repos, username)
        skill = skill_map().get(claim.get("skill_id", ""))
        if result["verified"] and skill:
            evidence_by_skill.setdefault(skill["id"], []).append(result["evidence"])
        else:
            failed = result["failed_checks"] or ["skill_id"]
            reasons = ", ".join(failed)
            rejected_claims.append(
                {
                    "claim": claim,
                    "failed_checks": failed,
                    "reason": f"Failed verification checks: {reasons}.",
                }
            )

    all_ids = set(resume_by_skill) | certified_ids | set(evidence_by_skill)
    all_ids.update(
        skill["id"]
        for skill in skill_map().values()
        if _deterministic_evidence(skill, repos, username)
    )
    skills = []
    for skill_id in sorted(all_ids):
        skill = skill_map().get(skill_id)
        if not skill:
            continue
        evidence = evidence_by_skill.get(skill_id, [])
        cited_files = {
            (item["repo"], item["file_path"]) for item in evidence if item["type"] == "code"
        }
        evidence.extend(
            item for item in _deterministic_evidence(skill, repos, username)
            # Skip a rule-based code citation when the AI already cited (and we verified) that file.
            if not (item["type"] == "code" and (item["repo"], item["file_path"]) in cited_files)
        )
        level = "Applied" if any(item["type"] == "code" for item in evidence) else (
            "Used" if evidence else "Claimed"
        )
        applied_repos = {
            item["repo"] for item in evidence if item["type"] == "code"
        }
        demonstrated = any(
            repo["name"] in applied_repos
            and (
                bool(repo.get("test_files"))
                or any(_is_ci_or_deploy(path) for path in repo.get("config_files", []))
            )
            for repo in repos
        )
        if level == "Applied" and demonstrated:
            level = "Demonstrated"
        skills.append(
            {
                "skill_id": skill_id,
                "level": level,
                "certified": skill_id in certified_ids,
                "evidence": evidence,
                "resume_mention": resume_by_skill.get(skill_id),
            }
        )

    return {"skills": skills, "rejected_claims": rejected_claims}


def verify_one_claim(claim: dict[str, Any], ingestion: dict[str, Any]) -> dict[str, Any]:
    return _verify_claim(claim, ingestion.get("repos", []), ingestion.get("username", ""))
