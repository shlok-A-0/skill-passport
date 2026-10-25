import hashlib
import posixpath
import re
import json
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from app.config import VERIFIER_VERSION
from app.services.ast_verifier import (
    js_has_signature,
    python_ast_has_signature,
    test_targets_implementation_with_assertions,
)
from app.services.framework import skill_map
from app.services.github_ingest import CI_DIRS, CI_FILES, DOCKER_FILES, is_test_path, module_stem

LEVEL_ORDER = {"Claimed": 0, "Used": 1, "Applied": 2, "Demonstrated": 3}
# The highest level a skill can reach under the ladder rules, where that is below Demonstrated.
# Unit Testing's code evidence is a test file itself, so Demonstrated would need another test
# targeting that test file; requirements above this level could never be met.
MAX_REACHABLE_LEVEL = {"unit-testing": "Applied"}


def reachable_min_level(skill_id: str, min_level: str) -> str:
    """A required level, capped at the highest level the skill can reach."""
    cap = MAX_REACHABLE_LEVEL.get(skill_id)
    if cap and LEVEL_ORDER[min_level] > LEVEL_ORDER[cap]:
        return cap
    return min_level
# Levels describe evidence of use, never ability. "Claimed" is shown as "Unverified".
LEVEL_DESCRIPTIONS = {
    "Claimed": "Mentioned on the resume or a certificate; no evidence in the analysed files.",
    "Used": "Listed in a dependency or config file the candidate committed to.",
    "Applied": "Used in source code the candidate committed to (exact lines cited).",
    "Demonstrated": "Applied, and exercised by tests the candidate committed to that target that code.",
}
LEVEL_NOTE = "Levels show evidence of use in the candidate's code, not how well the skill is used."
# Integrity signals are advisory: they are shown next to the evidence but never change
# verification or ladder level (a bulk upload is common for honest students too).
MIN_LINE_SHARE = 0.5
MIN_COMMIT_SHARE = 0.5
BULK_COMMIT_FILES = 20
BULK_COMMIT_ADDITIONS = 2000
MIN_REPO_COMMITS = 4
COMMIT_SPREAD_MIN_FILES = 15  # one-day history only matters for a sizeable repository
HISTORY_IMPORT_DAYS = 7
HISTORY_IMPORT_MIN_COMMITS = 3
BACKDATE_DAYS = 7
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
) -> dict[str, Any]:
    """How many cited lines git blame attributes to the candidate.

    Unknown (no penalty) when blame is missing, or when any cited line's author can't be
    identified (commit emails not linked to a GitHub account), since those may be the candidate's.
    """
    label = "Cited lines written by you"
    blame = source.get("blame")
    if not blame:
        return _signal("line_share", label, "unknown", "Line history (git blame) was not available.")
    cited = end - start + 1

    def overlap(range_start: int, range_end: int) -> int:
        return max(0, min(end, range_end) - max(start, range_start) + 1)

    yours = sum(overlap(s, e) for s, e, login in blame if _same_user(login, username))
    others = sum(overlap(s, e) for s, e, login in blame if login and not _same_user(login, username))
    if yours + others < cited:
        return _signal(
            "line_share", label, "unknown",
            f"{yours} of {cited} cited lines were last changed in your commits; the author of the other "
            f"{cited - yours - others} could not be identified (commit email not linked to a GitHub account).",
        )
    status = "ok" if yours / cited >= MIN_LINE_SHARE else "warn"
    return {
        **_signal("line_share", label, status, f"{yours} of {cited} cited lines were last changed in your commits."),
        "written": yours,
        "cited": cited,
    }


LINE_SHARE_DETAIL = re.compile(r"^(\d+) of (\d+) cited lines were last changed in your commits\.$")


def line_share(item: dict[str, Any]) -> tuple[int, int] | None:
    """(lines written by the candidate, lines cited), or None when blame couldn't determine it."""
    signal = next((s for s in item.get("integrity") or [] if s.get("id") == "line_share"), None)
    if not signal or signal.get("status") == "unknown":
        return None
    if isinstance(signal.get("written"), int) and isinstance(signal.get("cited"), int):
        return signal["written"], signal["cited"]
    match = LINE_SHARE_DETAIL.match(signal.get("detail", ""))  # passports stored before the counts existed
    return (int(match.group(1)), int(match.group(2))) if match else None


def lines_written(item: dict[str, Any]) -> dict[str, int] | None:
    """The line-share outcome shown next to the checks: {"written": N, "cited": M}, or None if unknown."""
    share = line_share(item)
    return {"written": share[0], "cited": share[1]} if share else None


def _share_allows_demonstrated(item: dict[str, Any]) -> bool:
    """A citation mostly written by others can support Applied, never Demonstrated (unknown: no penalty)."""
    share = line_share(item)
    return share is None or share[0] / share[1] >= MIN_LINE_SHARE


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
    status = "warn" if total < MIN_REPO_COMMITS else "ok"
    return {**_signal("repo_history", label, status, detail + "."), "total": total}


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _user_commit_dates(repo: dict[str, Any]) -> list[datetime]:
    return sorted(
        date for date in (
            _parse_utc(commit.get("author_date")) for commit in repo.get("user_commits") or []
        )
        if date is not None
    )


def _commit_spread_signal(repo: dict[str, Any]) -> dict[str, str]:
    label = "Work spread over time"
    dates = _user_commit_dates(repo)
    if not dates:
        return _signal("commit_spread", label, "unknown", "Your commit dates in this repository were not available.")
    more = "" if repo.get("user_commits_complete") else "+"
    days = sorted({date.date() for date in dates})
    spread = f"{len(dates)}{more} commits across {len(days)} days, from {days[0]} to {days[-1]}."
    if len(days) > 1:
        return _signal("commit_spread", label, "ok", spread)
    files = repo.get("source_file_count")
    if not repo.get("user_commits_complete") or not isinstance(files, int):
        # Older commits or the repository size are unknown, so one day can't be judged.
        return _signal(
            "commit_spread", label, "unknown",
            f"{len(dates)}{more} recent commits on {days[0]}; the rest of the history could not be compared.",
        )
    if files >= COMMIT_SPREAD_MIN_FILES:
        return _signal(
            "commit_spread", label, "warn",
            f"All {len(dates)} of your commits in this repository were made on one day ({days[0]}).",
        )
    return _signal("commit_spread", label, "ok", spread)


def _history_import_signal(repo: dict[str, Any]) -> dict[str, str]:
    label = "History matches when the repository was created"
    created = _parse_utc(repo.get("created_at"))
    dates = _user_commit_dates(repo)
    if created is None or not dates:
        return _signal(
            "history_import", label, "unknown",
            "Repository creation date or your commit dates were not available.",
        )
    # created_at is recorded by GitHub's servers; commit dates are set by the committer's machine.
    early = sum(1 for date in dates if date < created - timedelta(days=HISTORY_IMPORT_DAYS))
    created_day = created.date()
    if len(dates) >= HISTORY_IMPORT_MIN_COMMITS and early * 2 >= len(dates):
        return _signal(
            "history_import", label, "warn",
            f"{early} of {len(dates)} commits are dated before this repository was created on GitHub "
            f"({created_day}). The history may have been developed elsewhere, imported, or backdated.",
        )
    return _signal(
        "history_import", label, "ok",
        f"{early} of {len(dates)} commits are dated more than {HISTORY_IMPORT_DAYS} days before this "
        f"repository was created on GitHub ({created_day}).",
    )


def _backdated_commit_signal(repo: dict[str, Any]) -> dict[str, str]:
    """Advisory only. The author date can be set freely when committing; the committer date is
    rewritten when the commit is recorded, so a large gap means the commit was made much later
    than it claims (rebases and cherry-picks also do this)."""
    label = "Commit dates match when commits were recorded"
    pairs = [
        (author, committer)
        for author, committer in (
            (_parse_utc(commit.get("author_date")), _parse_utc(commit.get("committer_date")))
            for commit in repo.get("user_commits") or []
        )
        if author is not None and committer is not None
    ]
    if not pairs:
        return _signal(
            "backdated_commits", label, "unknown",
            "Author and committer dates of your commits were not available.",
        )
    late = sum(1 for author, committer in pairs if committer - author > timedelta(days=BACKDATE_DAYS))
    detail = f"{late} of {len(pairs)} commits were recorded more than {BACKDATE_DAYS} days after their author date."
    if len(pairs) >= HISTORY_IMPORT_MIN_COMMITS and late * 2 >= len(pairs):
        return _signal(
            "backdated_commits", label, "warn",
            f"{detail} The dates may have been set back; this also happens with rebases or cherry-picks of older work.",
        )
    return _signal("backdated_commits", label, "ok", detail)


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


def _commit_signature_signal(source: dict[str, Any]) -> dict[str, str]:
    verification = source.get("commit_verification")
    scope = source.get("commit_verification_scope", "head")
    label = "Head commit cryptographic signature" if scope == "head" else "Cryptographic commit signature"
    if not verification:
        return _signal("commit_signature", label, "unknown", "Commit signature verification was not available.")
    if verification.get("verified"):
        detail = (
            "Repository head commit is cryptographically signed (verified GPG/SSH key). "
            "Individual historical commit for cited lines was not separately verified."
            if scope == "head"
            else "Commit is cryptographically signed (verified GPG/SSH key)."
        )
        return _signal("commit_signature", label, "ok", detail)
    detail = (
        f"Repository head commit is standard Git commit ({verification.get('reason', 'unsigned')})."
        if scope == "head"
        else f"Commit is standard Git commit ({verification.get('reason', 'unsigned')})."
    )
    return _signal("commit_signature", label, "unknown", detail)


def integrity_signals(
    repo: dict[str, Any], source: dict[str, Any], start: int, end: int, username: str
) -> list[dict[str, str]]:
    return [
        _line_share_signal(source, start, end, username),
        _commit_share_signal(source, username),
        _bulk_upload_signal(source),
        _repo_history_signal(repo),
        _commit_spread_signal(repo),
        _history_import_signal(repo),
        _backdated_commit_signal(repo),
        _ai_leftover_signal(source),
        _commit_signature_signal(source),
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


# Skills whose Python evidence naturally lives in a string passed to a call: SQL in
# text("SELECT ...")/cursor.execute(...), connection URLs in connect("postgresql://..."), Docker CLI
# commands in subprocess.run("docker build ..."). Only these accept a signature inside a call's
# string argument; comments and docstrings never count.
STRING_SIGNATURE_SKILLS = {"sql", "postgresql", "mysql", "mongodb", "redis", "docker"}


def _signature_found(skill: dict[str, Any], path: str, lines: list[str], start: int, end: int) -> bool:
    extension = PurePosixPath(path).suffix.lower()
    extensions = {item.lower() for item in skill.get("file_extensions", [])}
    if skill.get("category") == "Language":
        # A language skill is evidenced by the file's language, nothing else.
        return extension in extensions
    if extensions and extension not in extensions:
        return False
    # Frameworks, tools and practices need an actual signature near the cited lines.
    signatures = skill.get("code_signatures", [])
    if extension == ".py":
        # Syntax-aware AST check for Python: verifies the citation binds to a real syntax node
        # rather than being inside a comment or string literal.
        full_content = "\n".join(lines)
        ast_result = python_ast_has_signature(
            full_content, start, end, signatures,
            string_arguments=skill.get("id") in STRING_SIGNATURE_SKILLS,
        )
        # Safe behaviour: invalid Python syntax or AST parsing failure strictly rejects signature
        return bool(ast_result)
    window = "\n".join(lines[max(0, start - 1 - 5): min(len(lines), end + 5)])
    if extension in {".js", ".jsx", ".ts", ".tsx"}:
        return js_has_signature(window, signatures)
    return any(
        _signature_pattern(signature).search(window)
        for signature in signatures
    )


def contains_signature(text: str, signature: str) -> bool:
    """Case-insensitive; a signature starting with a letter must start a word ("it(" is not in "emit(")."""
    prefix = r"(?<!\w)" if signature[:1].isalnum() or signature[:1] == "_" else ""
    return re.search(prefix + re.escape(signature), text, flags=re.IGNORECASE) is not None


def snippet_sha256(snippet: str) -> str:
    """Hash of the exact cited lines joined with "\\n"; any change to a line changes it."""
    return hashlib.sha256(snippet.encode("utf-8")).hexdigest()


def _attach_record(evidence: dict[str, Any], source: dict[str, Any]) -> None:
    """A reproducible record of what was checked, when, and by which rules (recruiter/badge only)."""
    evidence["record"] = {
        "commit_sha": evidence["commit_sha"],
        "path": evidence["file_path"],
        "start_line": evidence["start_line"],
        "end_line": evidence["end_line"],
        "snippet_sha256": snippet_sha256(evidence["snippet"]),
        "retrieved_at": source.get("retrieved_at"),
        "verifier_version": VERIFIER_VERSION,
        "checks": dict(evidence["checks"]),
        "rules_applied": ["applied:code_citation"],  # completed by compute_level()
    }


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
        evidence["lines_written"] = lines_written(evidence)
        _attach_record(evidence, source)
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
        evidence[-1]["lines_written"] = lines_written(evidence[-1])
        _attach_record(evidence[-1], source)
    return evidence


GIT_HISTORY_PATH = "(commit history)"
MAX_TEST_FILE_EVIDENCE = 3
MAX_SIGNATURE_CODE_EVIDENCE = 3  # a few files per skill is enough; more bloats passports


# --- deterministic scan: code-only matching ---------------------------------
# Taxonomy signatures are short substrings, so matching raw lines turns prose into
# recruiter-facing "code evidence": a comment "# select the best candidate" cited
# SQL, "# remember to git pull" cited git, and a docstring mentioning docker cited
# Docker. The scan therefore matches against a code-only view of each file (comment
# and string contents removed per line, line numbers preserved), applies path or
# command-position gates to weak signatures, matches case-sensitively where prose
# case differs from code case, and skips a file whose only match is an unused
# import. Where none of that shows actual code use, the scan abstains instead of
# citing weak evidence.

_C_LIKE_SUFFIXES = {"js", "jsx", "ts", "tsx", "java", "go", "cpp", "cc", "c", "h", "hpp"}
_SQL_SCAN_SIGS = {"SELECT ", "INSERT INTO ", "CREATE TABLE "}
_CI_SCAN_SIGS = {"jobs:", "runs-on:"}
_K8S_SCAN_SIGS = {"apiVersion:", "kind: Deployment"}
_COMMAND_SCAN_SIGS = {"docker run ", "docker build ", "docker compose ", "git "}
_CI_PATH_MARKERS = (
    ".github/workflows/", ".forgejo/workflows/", ".gitea/workflows/", ".gitlab-ci",
    ".circleci/", "jenkinsfile", "azure-pipelines", ".travis", "bitbucket-pipelines",
    "buildkite",
)


def _python_code_lines(lines: list[str]) -> list[tuple[int, str]] | None:
    """Comment/string contents blanked via tokenize (docstrings included); None on failure."""
    import io
    import tokenize

    chars = [list(line) for line in lines]
    try:
        for tok in tokenize.generate_tokens(io.StringIO("\n".join(lines)).readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                continue
            (srow, scol), (erow, ecol) = tok.start, tok.end
            for row in range(srow, erow + 1):
                start = scol if row == srow else 0
                end = ecol if row == erow else len(chars[row - 1])
                for col in range(start, end):
                    chars[row - 1][col] = " "
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return None
    return [(number, "".join(row)) for number, row in enumerate(chars, start=1)]


def _generic_code_lines(path: str, lines: list[str]) -> list[tuple[int, str]]:
    """State-machine blanking of comments and strings for non-Python sources."""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if suffix == "sql":
        line_markers, block_markers, quotes, multiline = ("--",), (("/*", "*/"),), ("'", '"'), ()
    elif suffix in _C_LIKE_SUFFIXES:
        line_markers, block_markers, quotes, multiline = ("//",), (("/*", "*/"),), ("'", '"', "`"), ("`",)
    else:  # shell and anything else: # comments, single/double quotes
        line_markers, block_markers, quotes, multiline = ("#",), (), ("'", '"'), ()
    code_lines: list[tuple[int, str]] = []
    state: tuple[str, str] | None = None
    for lineno, line in enumerate(lines, start=1):
        buf = list(line)
        i = 0
        while i < len(buf):
            if state is None:
                if lineno == 1 and i == 0 and line.startswith("#!"):
                    break  # shebang is code (Linux/Shell signature), not a comment
                if any(line.startswith(marker, i) for marker in line_markers):
                    for col in range(i, len(buf)):
                        buf[col] = " "
                    break
                block = next((b for a, b in block_markers if line.startswith(a, i)), None)
                if block is not None:
                    buf[i] = " "
                    if i + 1 < len(buf):
                        buf[i + 1] = " "
                    state = ("block", block)
                    i += 2
                    continue
                if buf[i] in quotes:
                    state = ("string", buf[i])
                    buf[i] = " "
                    i += 1
                    continue
                i += 1
            elif state[0] == "block":
                if line.startswith(state[1], i):
                    for col in range(i, min(len(buf), i + len(state[1]))):
                        buf[col] = " "
                    i += len(state[1])
                    state = None
                else:
                    buf[i] = " "
                    i += 1
            else:  # inside a string literal
                if buf[i] == "\\" and state[1] != "`":
                    buf[i] = " "
                    if i + 1 < len(buf):
                        buf[i + 1] = " "
                    i += 2
                    continue
                if buf[i] == state[1]:
                    state = None
                buf[i] = " " if state is not None else " "
                i += 1
        if state is not None and state[0] == "string" and state[1] not in multiline:
            state = None  # unterminated single-line string; next line starts fresh
        code_lines.append((lineno, "".join(buf)))
    return code_lines


def _code_only_lines(path: str, lines: list[str]) -> list[tuple[int, str]]:
    """(line number, comment/string-blanked text) per line; masking only removes matches."""
    if path.endswith(".py"):
        masked = _python_code_lines(lines)
        if masked is not None:
            return masked
    return _generic_code_lines(path, lines)


def _signature_applicable(signature: str, path: str) -> bool:
    """Path gates for signatures that are only meaningful in specific file kinds."""
    lower = path.lower()
    if signature in _CI_SCAN_SIGS:
        return any(marker in lower for marker in _CI_PATH_MARKERS)
    if signature in _K8S_SCAN_SIGS:
        return lower.endswith((".yaml", ".yml"))
    return True


def _in_command_position(line: str, signature: str) -> bool:
    """A shell command at statement start or after a separator, not prose mentioning it."""
    body = re.escape(signature.rstrip())
    pattern = r"(?:^|&&|\|\|\||[;|({]\s*)(?:sudo\s+)?" + body + r"(?:\s|$)"
    return re.search(pattern, line.lstrip()) is not None


def _scan_match(line: str, signature: str, path: str) -> bool:
    """Stricter contains_signature for the deterministic scan only.

    Command signatures (git, docker CLI) require command position outside shell files;
    signatures containing uppercase match case-sensitively so lowercase prose ("select
    the best") cannot cite SQL, while .sql files keep case-insensitive matching because
    their remaining code-only text is SQL by construction.
    """
    lower = path.lower()
    if signature in _COMMAND_SCAN_SIGS and not lower.endswith(".sh"):
        return _in_command_position(line, signature)
    case_insensitive = not any(char.isupper() for char in signature) or (
        lower.endswith(".sql") and signature in _SQL_SCAN_SIGS
    )
    prefix = r"(?<!\w)" if signature[:1].isalnum() or signature[:1] == "_" else ""
    flags = re.IGNORECASE if case_insensitive else 0
    return re.search(prefix + re.escape(signature), line, flags=flags) is not None


_IMPORT_LINE = re.compile(
    r"^\s*(?:import\b|from\s+[\w.]+\s+import\b|(?:const|let|var)\s+\w+\s*=\s*require\()"
)


def _imported_tokens(line: str) -> set[str]:
    """Names an import statement binds, to tell a used import from an unused one."""
    tokens: set[str] = set()
    stripped = line.strip()
    match = re.match(r"import\s+(.+)$", stripped)
    if match and " from " not in stripped:
        for part in match.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            alias = re.search(r"\bas\s+(\w+)$", part)
            tokens.add(alias.group(1) if alias else part.split(".")[0].strip())
        return tokens
    match = re.match(r"from\s+[\w.]+\s+import\s+(.+)$", stripped)
    if match:
        for part in match.group(1).strip().strip("()").split(","):
            part = part.strip()
            if not part or part == "*":
                continue
            alias = re.search(r"\bas\s+(\w+)$", part)
            tokens.add(alias.group(1) if alias else part.split()[0])
        return tokens
    match = re.match(r"(?:const|let|var)\s+(\w+)\s*=\s*require\(", stripped)
    if match:
        return {match.group(1)}
    match = re.match(r"import\s+(\w+)\s+from\b", stripped)
    if match:
        return {match.group(1)}
    match = re.match(r"import\s*\{([^}]*)\}", stripped)
    if match:
        for part in match.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            alias = re.search(r"\bas\s+(\w+)$", part)
            tokens.add(alias.group(1) if alias else part)
    return tokens


def _only_unused_imports(
    matches: list[tuple[int, str]], code_lines: list[tuple[int, str]]
) -> bool:
    """Every match is an import statement binding names the rest of the file never uses."""
    if not all(_IMPORT_LINE.match(cline) for _, cline in matches):
        return False
    tokens: set[str] = set()
    for _, cline in matches:
        tokens |= _imported_tokens(cline)
    if not tokens:
        return True  # side-effect import: no evidence of use
    matched_numbers = {number for number, _ in matches}
    for number, cline in code_lines:
        if number in matched_numbers:
            continue
        if any(re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", cline) for token in tokens):
            return False
    return True


def _signature_code_evidence(
    skill: dict[str, Any], repo: dict[str, Any], username: str = ""
) -> list[dict[str, Any]]:
    """Cite fetched, authored, non-test files whose code-only lines contain the skill's signatures.

    Deterministic coverage for skills the claim proposer never named. The scan only
    covers source files that were actually fetched and read, not the whole
    repository tree - absence here still means "not in the sample", not "absent".
    """
    evidence: list[dict[str, Any]] = []
    # Language skills are excluded: their signatures ("def ", "import ") match
    # nearly every file, so a deterministic citation would be noise. Languages
    # are proposed reliably by the claim proposer anyway; this scan exists for
    # framework/tool skills with distinctive signatures.
    if skill.get("category") == "Language":
        return evidence
    signatures = skill.get("code_signatures", [])
    if not signatures:
        return evidence
    for path, source in sorted((repo.get("source_files") or {}).items()):
        if len(evidence) >= MAX_SIGNATURE_CODE_EVIDENCE:
            break
        if is_test_path(path) or not source.get("authored_shas"):
            continue
        lines = source.get("lines", [])
        code_lines = _code_only_lines(path, lines)
        applicable = [sig for sig in signatures if _signature_applicable(sig, path)]
        matches = [
            (number, cline)
            for number, cline in code_lines
            if any(_scan_match(cline, sig, path) for sig in applicable)
        ]
        if not matches or _only_unused_imports(matches, code_lines):
            continue
        first = matches[0][0]
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
            "explanation": f"{skill['name']} usage in {path}, found by the deterministic scan.",
            "checks": {
                "file_exists": path in repo.get("tree", []),
                "lines_valid": True,
                "authored": True,
                "not_fork": not repo.get("is_fork", False),
                "signature_found": True,
            },
            "integrity": integrity_signals(repo, source, start, end, username),
        })
        evidence[-1]["lines_written"] = lines_written(evidence[-1])
        _attach_record(evidence[-1], source)
    return evidence


def _authored_commit_count(repo: dict[str, Any]) -> tuple[int, str]:
    """How many commits the user authored here (and "+" when the list was cut off)."""
    commits = repo.get("user_commits") or []
    if commits:
        return len(commits), "" if repo.get("user_commits_complete") else "+"
    by_user = (repo.get("commit_counts") or {}).get("by_user")
    if isinstance(by_user, int) and by_user > 0:
        return by_user, ""
    # Older ingestions: commits that touched the fetched files the user authored.
    shas = {
        sha for source in repo.get("source_files", {}).values()
        for sha in source.get("authored_shas") or []
    }
    return len(shas), ""


def _commit_evidence(repo: dict[str, Any], username: str) -> dict[str, Any] | None:
    """Commits the user authored in a non-fork repo show they use Git."""
    if repo.get("is_fork"):
        return None
    count, more = _authored_commit_count(repo)
    if not count:
        return None
    full_name = repo.get("full_name", repo["name"])
    owner = repo.get("owner") or full_name.split("/")[0]
    return {
        "type": "config",
        "repo": repo["name"],
        "file_path": GIT_HISTORY_PATH,
        "start_line": 0,
        "end_line": 0,
        "commit_sha": repo.get("head_sha") or "HEAD",
        "url": f"https://github.com/{full_name}/commits?author={owner}",
        "snippet": "",
        "explanation": f"Git: {count}{more} commit(s) authored by you in this repository.",
        # Authored by construction, so it counts under "Used requires authorship".
        "checks": {"file_exists": True, "authored": True, "not_fork": True},
    }


def _test_file_evidence(repo: dict[str, Any], path: str) -> dict[str, Any]:
    """A test file's presence; its content isn't fetched, so there are no lines to cite."""
    sha = repo.get("head_sha") or "HEAD"
    return {
        "type": "config",
        "repo": repo["name"],
        "file_path": path,
        "start_line": 0,
        "end_line": 0,
        "commit_sha": sha,
        "url": f"https://github.com/{repo.get('full_name', repo['name'])}/blob/{sha}/{quote(path, safe='/')}",
        "snippet": "",
        "explanation": "Test file present in the repository.",
        "checks": {
            "file_exists": path in repo.get("tree", []),
            "authored": bool((repo.get("test_authored") or {}).get(path)),
            "not_fork": not repo.get("is_fork", False),
        },
        "test_file_group": True,
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
            code = _test_code_evidence(skill, repo, username)
            evidence.extend(code)
            # A few test files per repo is enough proof; listing all of them bloated passports.
            cited = {item["file_path"] for item in code}
            remaining_test_files = [path for path in repo["test_files"] if path not in cited]
            remaining_test_files.sort(
                key=lambda path: not committed_to_test(repo, path)
            )
            evidence.extend(
                _test_file_evidence(repo, path)
                for path in remaining_test_files[:MAX_TEST_FILE_EVIDENCE]
            )
        if skill["id"] == "git":
            commit = _commit_evidence(repo, username)
            if commit:
                evidence.append(commit)
        # Code-level coverage for skills with no (or no verified) claim: cite
        # fetched authored files carrying the skill's signatures. Duplicates of
        # files the proposer already cited are filtered by the caller.
        evidence.extend(_signature_code_evidence(skill, repo, username))
    return evidence


def _path_authored(repo: dict[str, Any], path: str) -> bool:
    """Whether the user committed to this dependency/config file; missing data means no."""
    return bool((repo.get("authored_paths") or {}).get(path, False))


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
            "authored": _path_authored(repo, path),
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
    is_ci = path.startswith(CI_DIRS) or path in CI_FILES
    ci_status = (repo.get("ci_status") or {}) if is_ci else None

    checks = {
        "file_exists": path in repo.get("tree", []),
        "lines_valid": bool(content) and end <= len(lines),
        "authored": _path_authored(repo, path),
        "not_fork": not repo.get("is_fork", False),
        "signature_found": True,
    }
    explanation = f"{skill_name} is referenced by {path}."
    # CI run status is context, not a check: a beginner's workflow that never ran or failed
    # still shows they wrote it.
    ci_run = None
    if is_ci and ci_status is not None:
        if ci_status.get("has_successful_run"):
            ci_run = {"verified": True, "status": "passed"}
            explanation = f"{skill_name} workflow in {path}; {ci_status.get('summary', 'all checks passed')} on {sha[:7]}."
        elif ci_status.get("status") == "failed":
            ci_run = {"verified": False, "status": "failed"}
            explanation = f"{skill_name} workflow in {path}; CI check run failed ({ci_status.get('summary', 'checks failed')})."
        elif ci_status.get("status") == "in_progress":
            ci_run = {"verified": False, "status": "in_progress"}
            explanation = f"{skill_name} workflow in {path}; CI checks currently in progress ({ci_status.get('summary')})."
        else:
            ci_run = {"verified": False, "status": "none"}
            explanation = f"{skill_name} workflow in {path} (no recorded CI runs on GitHub Actions)."

    return {
        "type": evidence_type,
        "repo": repo["name"],
        "file_path": path,
        "start_line": 1,
        "end_line": end,
        "commit_sha": sha,
        "url": _make_url(repo, sha, path, 1, end),
        "snippet": "\n".join(lines[:end]),
        "explanation": explanation,
        "checks": checks,
        **({"ci_run": ci_run} if ci_run else {}),
    }


def counts_for_level(skill_id: str, item: dict[str, Any]) -> bool:
    if item.get("type") not in {"dependency", "config"}:
        return True  # code and commit evidence are authored by construction
    return bool((item.get("checks") or {}).get("authored"))


def compute_level(
    skill_id: str, evidence: list[dict[str, Any]], repos: list[dict[str, Any]]
) -> str:
    """Evidence Ladder level from the evidence types (section 3.1); rules only, never the LLM.

    Dependency and config evidence only counts towards Used when the user committed to
    that file; unauthored items stay attached for transparency but don't raise the level.
    """
    counted = [item for item in evidence if counts_for_level(skill_id, item)]
    level = "Applied" if any(item["type"] == "code" for item in counted) else (
        "Used" if counted else "Claimed"
    )
    # Demonstrated is skill-specific: one of this skill's counted citations (line share 50%+ or
    # unknown) must be targeted by a test in the same repo that the candidate committed to.
    # A test, CI workflow or Dockerfile elsewhere never upgrades it.
    by_name = {repo["name"]: repo for repo in repos}
    demonstrated = False
    for item in evidence:
        if item.get("type") != "code":
            continue
        repo = by_name.get(item["repo"]) or {}
        item.pop("demonstrated_by", None)
        target = (
            targeting_test(item["file_path"], repo, committed_only=True)
            if _share_allows_demonstrated(item) else None
        )
        rules = ["applied:code_citation"]
        if target:
            item["demonstrated_by"] = target
            demonstrated = True
            ci_status = repo.get("ci_status") or {}
            if ci_status.get("has_successful_run"):
                rules.append(f"demonstrated:test_{target['match']}_match_ci_verified")
            else:
                rules.append(f"demonstrated:test_{target['match']}_match")
        if "record" in item:
            item["record"]["rules_applied"] = rules
        # Context only, never a level change.
        item["build_config_present"] = any(_is_ci_or_deploy(path) for path in repo.get("config_files", []))
    if level == "Applied" and demonstrated:
        level = "Demonstrated"
    return level


def _name_targets(test_path: str, stem: str) -> bool:
    """`engine` -> test_engine.py, engine_test.py, engine.test.js, engine.spec.ts, tests/test_engine_x.py."""
    name = PurePosixPath(test_path).name.lower()
    return re.search(rf"(?<![a-z0-9]){re.escape(stem)}", name) is not None


def _imports_module(content: str, stem: str) -> bool:
    """Python `from x.engine import` / `import engine`; JS `require('./engine')` / `from '../engine'`."""
    module = re.escape(stem)
    python = rf"(?m)^\s*(?:from\s+[\w.]*\b{module}\s+import\b|import\s+(?:[\w.]*\.)?{module}\b)"
    javascript = rf"""(?:require\(\s*|from\s+|import\s+)['"][^'"]*\b{module}(?:\.[cm]?[jt]sx?)?['"]"""
    return bool(re.search(python, content) or re.search(javascript, content))


# Package entry points: importing anything from the package would look like importing these.
NO_IMPORT_FALLBACK_STEMS = {"__init__", "index"}
JS_IMPORT_SPEC = re.compile(r"""(?:require\(\s*|import\s*\(\s*|from\s+|import\s+)['"]([^'"]+)['"]""")


def _explicitly_imports(content: str, test_path: str, cited_path: str) -> bool:
    """Whether a test imports the cited file by its own module path, for stems too short or
    generic to match by name: `from app.db import x`, `import app.db`, `from app import db`,
    `require('./db')`. A name merely containing the stem never matches."""
    cited = PurePosixPath(cited_path)
    if cited.suffix == ".py":
        parts = cited.with_suffix("").parts
        module = ".".join(parts)
        parent, _, name = module.rpartition(".")
        patterns = [rf"(?m)^\s*from\s+{re.escape(module)}\s+import\b",
                    rf"(?m)^\s*import\s+{re.escape(module)}\b(?!\.)"]
        if parent:
            patterns.append(rf"(?m)^\s*from\s+{re.escape(parent)}\s+import\s+[^\n]*\b{re.escape(name)}\b")
        return any(re.search(pattern, content) for pattern in patterns)
    target = str(cited.with_suffix(""))
    for spec in JS_IMPORT_SPEC.findall(content):
        if not spec.startswith("."):
            continue
        resolved = posixpath.normpath(posixpath.join(str(PurePosixPath(test_path).parent), spec))
        if resolved in {target, cited_path}:
            return True
    return False


def committed_to_test(repo: dict[str, Any], path: str) -> bool:
    """Whether the candidate committed to this test file; unknown (lookup failed or not read) is False."""
    authored = (repo.get("test_authored") or {}).get(path)
    if authored is not None:
        return bool(authored)
    source = (repo.get("source_files") or {}).get(path)
    return bool(source and source.get("authored_shas"))


def targeting_test(
    cited_path: str, repo: dict[str, Any], committed_only: bool = False
) -> dict[str, Any] | None:
    """The test file in `repo` that targets `cited_path` by name or by import, if any.

    With `committed_only`, only tests the candidate committed to count (the "Demonstrated" rule).
    """
    stem = module_stem(cited_path)
    # Too short or generic (db, main, utils, app) to match by name; such a file can still be
    # targeted by a test that explicitly imports its module path. Package entry points can't.
    fallback = None if stem else PurePosixPath(cited_path).stem.lower()
    if not stem and (not fallback or fallback in NO_IMPORT_FALLBACK_STEMS):
        return None

    contents = dict(repo.get("test_contents") or {})
    for path, source in (repo.get("source_files") or {}).items():
        if is_test_path(path):
            contents.setdefault(path, source.get("content", ""))

    def eligible(path: str) -> bool:
        if path == cited_path:
            return False
        if committed_only:
            if not committed_to_test(repo, path):
                return False
            content = contents.get(path)
            if content is None:
                source = (repo.get("source_files") or {}).get(path)
                content = source.get("content") if source else None
            # Meaningful test check: if test content is available, it must contain executable assertions
            # and actively exercise or reference the cited implementation/module.
            if content is not None:
                if not content.strip() or not test_targets_implementation_with_assertions(
                        content, path, stem or fallback, cited_path):
                    return False
        return True

    if not stem:
        for path, content in contents.items():
            if eligible(path) and _explicitly_imports(content, path, cited_path):
                return {"path": path, "match": "import"}
        return None
    for path in repo.get("test_files", []):
        if eligible(path) and _name_targets(path, stem):
            return {"path": path, "match": "name"}
    for path, content in contents.items():
        if eligible(path) and _imports_module(content, stem):
            return {"path": path, "match": "import"}
    return None


# Older stored passports used "copied"; both mean the same excluded outcome.
EARLIER_SOURCE_KINDS = {"earlier_source", "copied"}
NOT_WRITTEN_KIND = "not_written_by_candidate"
NOT_WRITTEN_REASON = "None of the cited lines were last changed in your commits."
# Citations removed from a skill after verification; students see each one and can request a review.
EXCLUDED_KINDS = EARLIER_SOURCE_KINDS | {NOT_WRITTEN_KIND}
# Shown next to each code citation. "authored" only means the candidate committed to the file.
CHECK_LABELS = {
    "file_exists": "File exists in repository",
    "lines_valid": "Cited lines are valid",
    "authored": "You committed to this file",
    "not_fork": "Repository is not a fork",
    "signature_found": "Skill signature found in code",
    "lines_written": "Cited lines written by you",
    "ci_run_verified": "Verified passing CI run",  # older stored passports only
}


def citation_key(claim: dict[str, Any]) -> str:
    """Stable id for one cited range: "{skill_id}:{repo}:{file_path}:{start}-{end}"."""
    return (
        f"{claim.get('skill_id')}:{claim.get('repo')}:{claim.get('file_path')}:"
        f"{claim.get('start_line')}-{claim.get('end_line')}"
    )


def _earlier_source(item: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (
            signal for signal in item.get("integrity", [])
            if signal.get("id") == "copy_match" and signal.get("earlier_source")
        ),
        None,
    )


def propagate_earlier_sources_per_file(skills: list[dict[str, Any]]) -> None:
    """Give every code citation in a file the confirmed earlier-source signal found for that file.

    The copy check searches each citation's distinctive lines, so a citation made only of
    boilerplate (imports, `app = Flask(__name__)`, short lines) has nothing to search and passed
    even when another citation from the same file was excluded as an earlier public source.
    Only a confirmed exclusion (two lines in an earlier file) spreads; a one-line advisory match
    does not. Reuses the signals already attached, so no extra GitHub calls.
    """
    sources: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    for skill in skills:
        for item in skill.get("evidence", []):
            signal = _earlier_source(item) if item.get("type") == "code" else None
            if signal is not None:
                sources.setdefault((item["repo"], item["file_path"]), (signal, item))
    for skill in skills:
        for item in skill.get("evidence", []):
            found = sources.get((item.get("repo"), item.get("file_path")))
            if item.get("type") != "code" or found is None or _earlier_source(item) is not None:
                continue
            signal, origin = found
            item["integrity"] = [
                *(other for other in item.get("integrity", []) if other.get("id") != "copy_match"),
                {
                    **signal,
                    "detail": (
                        f"Excluded because another citation in this file (lines {origin['start_line']}-"
                        f"{origin['end_line']}) matched an earlier public source. {signal.get('detail', '')}"
                    ),
                },
            ]


def _excluded_claim(skill_id: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "repo": item["repo"],
        "file_path": item["file_path"],
        "start_line": item["start_line"],
        "end_line": item["end_line"],
        "explanation": item.get("explanation", ""),
    }


def _exclusion(skill_id: str, item: dict[str, Any]) -> dict[str, Any] | None:
    """Why this code citation can't count as the candidate's work, or None if it counts."""
    if item.get("type") != "code":
        return None
    signal = _earlier_source(item)
    if signal is not None:
        return {
            "claim": _excluded_claim(skill_id, item),
            "failed_checks": ["earlier_source"],
            "reason": signal.get("detail", ""),
            "url": item.get("url"),
            "source_repo": signal.get("source_repo"),
            "source_url": signal.get("source_url"),
            "source_first_seen": signal.get("source_first_seen"),
            "candidate_first_seen": signal.get("candidate_first_seen"),
            "kind": "earlier_source",
        }
    share = line_share(item)
    if share is not None and share[0] == 0:
        return {
            "claim": _excluded_claim(skill_id, item),
            "failed_checks": [NOT_WRITTEN_KIND],
            "reason": NOT_WRITTEN_REASON,
            "url": item.get("url"),
            "lines_written": {"written": 0, "cited": share[1]},
            "kind": NOT_WRITTEN_KIND,
        }
    return None


def exclude_uncounted_citations(
    verified: dict[str, Any], repos: list[dict[str, Any]]
) -> dict[str, Any]:
    """Remove code citations that can't be attributed to the candidate, and recompute levels.

    Two outcomes exclude a citation: an earlier public source (two different cited lines in a
    file that appeared more than a day earlier elsewhere; "can't attribute", not "copied"), and
    blame showing none of the cited lines were last changed by the candidate. Unknown blame and
    every other integrity signal stay advisory. An earlier source applies to the whole file.
    """
    propagate_earlier_sources_per_file(verified["skills"])
    kept_skills = []
    for skill in verified["skills"]:
        kept = []
        for item in skill["evidence"]:
            exclusion = _exclusion(skill["skill_id"], item)
            if exclusion is None:
                kept.append(item)
            else:
                verified["rejected_claims"].append(exclusion)
        if len(kept) != len(skill["evidence"]):
            skill["evidence"] = kept
            skill["level"] = compute_level(skill["skill_id"], kept, repos)
            if not kept and not skill.get("resume_mention") and not skill.get("certified"):
                continue  # nothing left that would list this skill
        kept_skills.append(skill)
    verified["skills"] = kept_skills
    return verified


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
                    "kind": "unsupported",
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
        skills.append(
            {
                "skill_id": skill_id,
                "name": skill["name"],
                "level": compute_level(skill_id, evidence, repos),
                "certified": skill_id in certified_ids,
                "evidence": evidence,
                "resume_mention": resume_by_skill.get(skill_id),
            }
        )

    return {"skills": skills, "rejected_claims": rejected_claims}


def verify_one_claim(claim: dict[str, Any], ingestion: dict[str, Any]) -> dict[str, Any]:
    return _verify_claim(claim, ingestion.get("repos", []), ingestion.get("username", ""))
