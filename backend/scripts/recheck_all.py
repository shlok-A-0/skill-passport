"""Rebuild every stored passport with a fresh GitHub read (the same logic as Re-check).

Run from the project root with the backend venv's Python:

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\recheck_all.py

Prints one line per passport: username, requirements met before -> after, and the
number of rejected (unsupported) and excluded (confirmed copy) claims. A passport that
fails is reported and left unchanged; the others still run.
"""
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.pipeline import recheck_passport  # noqa: E402
from app.services.storage import list_passports  # noqa: E402


def summary_line(old: dict[str, Any], new: dict[str, Any]) -> str:
    rejected = new.get("rejected_claims", [])
    excluded = sum(1 for item in rejected if item.get("kind") == "copied")
    total = new.get("role_match", {}).get("total", "?")
    return (
        f"{new['github_username']:<20} met {old.get('role_match', {}).get('met', '?')} -> "
        f"{new.get('role_match', {}).get('met', '?')} of {total}  "
        f"rejected {len(rejected) - excluded}  excluded {excluded}"
    )


def main() -> int:
    passports = list_passports()
    print(f"Re-checking {len(passports)} passport(s)...")
    failures = 0
    for old in passports:
        try:
            new = recheck_passport(old)
        except Exception as exc:  # report and continue with the next passport
            failures += 1
            print(f"{old.get('github_username', '?'):<20} FAILED: {exc.__class__.__name__}: {exc}")
            continue
        line = summary_line(old, new)
        if new.get("generation_error"):
            line += "  (growth plan not generated)"
        print(line, flush=True)
    print(f"Done: {len(passports) - failures} rebuilt, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
