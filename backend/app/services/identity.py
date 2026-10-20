"""Account-ownership proof and identity signals.

Every other check shows that code was written by a GitHub account; none shows that the person
applying controls that account, so anyone could submit a well-known developer's username. The
applicant proves control by publishing a public gist whose description is a one-time code issued
here (no OAuth app needed). For non-demo passports this is a hard gate before the job starts.
A successful proof is recorded against the one passport whose creation it authorised, so a proof
for a username never makes another passport for that username show as proven.

Two advisory signals are stored with the passport for recruiters only: whether ownership was
proven for this passport, and whether the resume links to the same GitHub account.
"""

import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from app.config import GITHUB_TOKEN
from app.services import storage

CHALLENGE_TTL = timedelta(hours=1)
CODE_PREFIX = "skill-passport-verify-"
OWNERSHIP_LABEL = "Applicant controls this GitHub account"
RESUME_LINK_LABEL = "Resume links to this GitHub account"
# github.com/<name>[/repo]: GitHub usernames are letters, digits and single hyphens, max 39.
# The lookbehind skips gist.github.com, api.github.com and lookalike domains.
GITHUB_LINK = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)(?![A-Za-z0-9-])",
    re.IGNORECASE,
)
# First path segments on github.com that are not user accounts.
RESERVED_PATHS = {
    "about", "account", "apps", "blog", "codespaces", "collections", "contact", "copilot",
    "customer-stories", "dashboard", "discussions", "education", "enterprise", "events",
    "explore", "features", "issues", "join", "login", "logout", "marketplace", "new",
    "notifications", "orgs", "organizations", "pricing", "pulls", "readme", "search",
    "security", "settings", "site", "sponsors", "team", "topics", "trending", "users",
}


class OwnershipError(Exception):
    """The applicant has not shown that they control the GitHub account."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect() -> sqlite3.Connection:
    # The storage module's connection, so tests that monkeypatch storage.DATABASE_PATH work.
    storage.initialize_storage()
    connection = storage._connect()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ownership_challenges (
            id TEXT PRIMARY KEY,
            github_username TEXT NOT NULL,
            code TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            verified_at TEXT,
            gist_url TEXT,
            passport_id TEXT
        )
        """
    )
    try:  # databases created before proofs were bound to a passport
        connection.execute("ALTER TABLE ownership_challenges ADD COLUMN passport_id TEXT")
    except sqlite3.OperationalError:
        pass
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ownership_challenges_unused_expiry
        ON ownership_challenges (expires_at)
        WHERE verified_at IS NULL
        """
    )
    return connection


def _same_login(login: str | None, username: str) -> bool:
    return bool(login) and login.lower() == username.lower()


def create_challenge(github_username: str) -> dict[str, Any]:
    username = (github_username or "").strip()
    if not username:
        raise OwnershipError("A GitHub username is required.")
    now = _now()
    challenge = {
        "challenge_id": secrets.token_urlsafe(16),
        "github_username": username,
        "code": CODE_PREFIX + secrets.token_hex(6),
        "expires_at": (now + CHALLENGE_TTL).isoformat(),
    }
    with _connect() as connection:
        # Keep verified rows: ownership_for_passport() uses them to build recruiter signals.
        # Unused codes have no lasting value once expired and can be safely discarded.
        connection.execute(
            "DELETE FROM ownership_challenges WHERE verified_at IS NULL AND expires_at <= ?",
            (now.isoformat(),),
        )
        connection.execute(
            """
            INSERT INTO ownership_challenges (id, github_username, code, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (challenge["challenge_id"], username, challenge["code"], now.isoformat(), challenge["expires_at"]),
        )
    return challenge


def _fetch_recent_gists(username: str) -> list[dict[str, Any]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Skill-Passport-Prototype",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        response = httpx.get(
            f"https://api.github.com/users/{quote(username, safe='')}/gists",
            params={"per_page": 30},
            headers=headers,
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise OwnershipError("Could not reach GitHub to check the gist. Please try again.") from exc
    if response.status_code == 404:
        raise OwnershipError("We couldn’t find that GitHub account. Check the username spelling and try again.")
    if response.status_code in (403, 429):
        remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
        message = response.reason_phrase
        try:
            body = response.json()
            if isinstance(body, dict):
                message = body.get("message", message)
        except (ValueError, AttributeError):
            pass
        if response.status_code == 429 or remaining == "0" or "rate limit" in str(message).lower():
            raise OwnershipError("GitHub is temporarily rate-limited. Please wait a few minutes and try again.")
        raise OwnershipError(
            "GitHub denied access while checking public gists. Please try again later; "
            "contact the site administrator if this continues."
        )
    if response.status_code == 401:
        raise OwnershipError("The server couldn’t authenticate with GitHub. Please contact the site administrator.")
    if not response.is_success:
        raise OwnershipError(
            f"GitHub returned an error ({response.status_code}) while checking gists. Please try again."
        )
    try:
        gists = response.json()
    except ValueError as exc:
        raise OwnershipError("GitHub returned an unreadable gist list. Please try again.") from exc
    return gists if isinstance(gists, list) else []


def verify_challenge(challenge_id: str | None, github_username: str, passport_id: str) -> dict[str, Any]:
    """Check that `github_username` published the challenge code as a public gist. Single use.

    On success the proof is recorded against `passport_id`, the passport whose creation it
    authorises; it never counts for any other passport.
    """
    if not passport_id:
        raise ValueError("A proof must be bound to the passport it authorises.")
    if not challenge_id:
        raise OwnershipError(
            "Prove this GitHub account is yours first: get a verification code and publish it as a public gist."
        )
    username = (github_username or "").strip()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM ownership_challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
    if not row:
        raise OwnershipError("Verification code not found. Get a new code and try again.")
    if not _same_login(row["github_username"], username):
        raise OwnershipError("This verification code was issued for a different GitHub username.")
    if row["verified_at"]:
        raise OwnershipError("This verification code was already used. Get a new code.")
    if _now() >= datetime.fromisoformat(row["expires_at"]):
        raise OwnershipError("This verification code has expired. Get a new code.")

    code = row["code"]
    gist = next(
        (
            item for item in _fetch_recent_gists(username)
            if code in (item.get("description") or "")
            and _same_login((item.get("owner") or {}).get("login"), username)
            and item.get("public")
        ),
        None,
    )
    if not gist:
        raise OwnershipError(
            f"No public gist with the description '{code}' was found on the GitHub account '{username}'. "
            "Create it at https://gist.github.com and try again."
        )

    verified_at = _now().isoformat()
    gist_url = gist.get("html_url")
    with _connect() as connection:
        # Only an unused code can be marked, so two simultaneous requests can't both succeed, and
        # the passport it authorises is recorded in the same step.
        cursor = connection.execute(
            """
            UPDATE ownership_challenges SET verified_at = ?, gist_url = ?, passport_id = ?
            WHERE id = ? AND verified_at IS NULL
            """,
            (verified_at, gist_url, passport_id, challenge_id),
        )
    if cursor.rowcount == 0:
        raise OwnershipError("This verification code was already used. Get a new code.")
    return {"verified": True, "method": "gist", "verified_at": verified_at, "gist_url": gist_url}


def ownership_for_passport(passport_id: str) -> dict[str, Any] | None:
    """The successful ownership proof recorded for this passport's creation, if any."""
    if not passport_id:
        return None
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT github_username, verified_at, gist_url FROM ownership_challenges
            WHERE passport_id = ? AND verified_at IS NOT NULL
            ORDER BY verified_at DESC
            LIMIT 1
            """,
            (passport_id,),
        ).fetchone()
    return dict(row) if row else None


def resume_github_logins(text: str) -> list[str]:
    """Lower-cased GitHub usernames from github.com/<name>[/repo] links, first appearance first."""
    logins: list[str] = []
    for match in GITHUB_LINK.finditer(text or ""):
        login = match.group(1).lower()
        if login not in RESERVED_PATHS and login not in logins:
            logins.append(login)
    return logins


def _signal(signal_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": signal_id, "label": label, "status": status, "detail": detail}


def ownership_signal(passport_id: str, github_username: str, is_demo: bool) -> dict[str, str]:
    """ok only when this passport's own creation was authorised by a proof for this account."""
    if is_demo:
        return _signal(
            "account_ownership", OWNERSHIP_LABEL, "unknown",
            "Demo passport (DEMO DATA); account ownership is not checked.",
        )
    proof = ownership_for_passport(passport_id)
    if proof and _same_login(proof["github_username"], github_username):
        return {
            **_signal(
                "account_ownership", OWNERSHIP_LABEL, "ok",
                f"Proven on {proof['verified_at'][:10]} for this passport: the applicant posted a one-time "
                "code as a public gist from this account.",
            ),
            "verified_at": proof["verified_at"],
            "gist_url": proof["gist_url"],
        }
    return _signal(
        "account_ownership", OWNERSHIP_LABEL, "warn",
        "This passport was created without proof that the applicant controls this GitHub account. "
        "Anyone can enter a public username; ask the candidate to verify.",
    )


def resume_link_signal(github_username: str, logins: list[str]) -> dict[str, str]:
    if not logins:
        return _signal(
            "resume_github_link", RESUME_LINK_LABEL, "unknown",
            "The resume has no github.com profile links.",
        )
    if github_username.strip().lower() in logins:
        return _signal(
            "resume_github_link", RESUME_LINK_LABEL, "ok",
            f"The resume links to github.com/{github_username.strip()}.",
        )
    linked = ", ".join(f"github.com/{login}" for login in logins)
    return _signal(
        "resume_github_link", RESUME_LINK_LABEL, "warn",
        f"The resume links to {linked}, not to this account. This could be an organisation or a "
        "collaborator's repository; ask the candidate.",
    )


def identity_signals(
    passport_id: str,
    github_username: str,
    is_demo: bool,
    resume_logins: list[str] | None,
    previous: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """[account_ownership, resume_github_link]. `resume_logins` is None on a re-check, where the
    resume isn't stored, so the earlier resume signal is kept."""
    if resume_logins is None:
        resume = next(
            (item for item in previous or [] if item.get("id") == "resume_github_link"),
            None,
        ) or _signal(
            "resume_github_link", RESUME_LINK_LABEL, "unknown",
            "Not checked: the resume was not re-read.",
        )
    else:
        resume = resume_link_signal(github_username, resume_logins)
    return [ownership_signal(passport_id, github_username, is_demo), resume]
