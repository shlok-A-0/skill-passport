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
TRUSTED_PROXY_IPS = frozenset(
    address.strip()
    for address in os.getenv("TRUSTED_PROXY_IPS", "").split(",")
    if address.strip()
)
# Bump whenever verification or level rules change; stored in every evidence record.
VERIFIER_VERSION = "2026-10-16"
# Environment and access control
APP_ENV = os.getenv("APP_ENV", "production").lower()
# When set or in production, recruiter review endpoints enforce fail-closed authentication.
REQUIRE_RECRUITER_AUTH = (
    os.getenv("REQUIRE_RECRUITER_AUTH", "").lower() in ("true", "1", "yes")
    or APP_ENV != "development"
)
# When set, the recruiter review endpoints require a matching X-Recruiter-Key header.
RECRUITER_KEY = os.getenv("RECRUITER_KEY", "")
# Debug endpoints require explicit opt-in in every environment.
ALLOW_DEBUG_ENDPOINTS = os.getenv("ALLOW_DEBUG_ENDPOINTS", "false").lower() in ("true", "1", "yes")
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = BASE_DIR / "skill_passport.db"
