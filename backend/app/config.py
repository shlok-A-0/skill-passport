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
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = BASE_DIR / "skill_passport.db"
