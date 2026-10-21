import ipaddress
import json
import os
from pathlib import Path

from dotenv import load_dotenv

try:
    # Use the operating system's certificate store, so HTTPS works behind antivirus or
    # corporate proxies that Python's bundled certificates don't know about.
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]


def _normalize_ip(address: str) -> str | None:
    try:
        return str(ipaddress.ip_address(address.strip()))
    except ValueError:
        return None


TRUSTED_PROXY_IPS = frozenset(
    normalized
    for address in os.getenv("TRUSTED_PROXY_IPS", "").split(",")
    if (normalized := _normalize_ip(address)) is not None
)
# Bump whenever verification or level rules change; stored in every evidence record.
VERIFIER_VERSION = "2026-10-21"
# Non-demo passports need a public-gist proof that the applicant controls the GitHub account.
# Only an explicit false value turns it off (development only, enforced at startup); an
# unrecognised value keeps it on and makes startup fail, so a typo never disables the proof.
_OWNERSHIP_PROOF_SETTING = (os.getenv("REQUIRE_OWNERSHIP_PROOF") or "true").strip().lower()
OWNERSHIP_PROOF_SETTING_VALID = _OWNERSHIP_PROOF_SETTING in ("true", "1", "yes", "false", "0", "no")
REQUIRE_OWNERSHIP_PROOF = _OWNERSHIP_PROOF_SETTING not in ("false", "0", "no")
# Environment and access control
APP_ENV = os.getenv("APP_ENV", "production").lower()
# When set or in production, recruiter review endpoints enforce fail-closed authentication.
REQUIRE_RECRUITER_AUTH = (
    os.getenv("REQUIRE_RECRUITER_AUTH", "").lower() in ("true", "1", "yes")
    or APP_ENV != "development"
)
# When set, the recruiter review endpoints require a matching X-Recruiter-Key header.
RECRUITER_KEY = os.getenv("RECRUITER_KEY", "")
# Production uses individually named keys so one recruiter can be revoked without rotating
# everyone else's credential. Example: {"recruiter-a":"<random secret>", ...}.
_RECRUITER_ACCOUNTS_RAW = os.getenv("RECRUITER_ACCOUNTS", "")
try:
    _recruiter_accounts = json.loads(_RECRUITER_ACCOUNTS_RAW or "{}")
except json.JSONDecodeError:
    _recruiter_accounts = None
RECRUITER_ACCOUNTS_SETTING_VALID = (
    isinstance(_recruiter_accounts, dict)
    and all(
        isinstance(name, str) and 1 <= len(name) <= 64
        and isinstance(secret, str) and len(secret) >= 32
        for name, secret in _recruiter_accounts.items()
    )
    and len(set(_recruiter_accounts.values())) == len(_recruiter_accounts)
)
RECRUITER_ACCOUNTS = _recruiter_accounts if RECRUITER_ACCOUNTS_SETTING_VALID else {}


def _feature_flag(name: str) -> bool:
    """On unless explicitly set to false/0/no."""
    return (os.getenv(name) or "true").strip().lower() not in ("false", "0", "no")


# Target roles built from a pasted job description (POST /roles/from-description).
ENABLE_CUSTOM_ROLES = _feature_flag("ENABLE_CUSTOM_ROLES")
# Project reports and a portfolio URL as extra (claim-only) sources on /passport/create.
ENABLE_REPORTS = _feature_flag("ENABLE_REPORTS")
# Debug endpoints require explicit opt-in in every environment.
ALLOW_DEBUG_ENDPOINTS = os.getenv("ALLOW_DEBUG_ENDPOINTS", "false").lower() in ("true", "1", "yes")
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = BASE_DIR / "skill_passport.db"
