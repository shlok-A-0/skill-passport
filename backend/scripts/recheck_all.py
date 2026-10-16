"""Rebuild stored passports with a fresh GitHub read (the same logic as Re-check).

Run from the project root with the backend venv's Python:

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\recheck_all.py [--only-incomplete]

--only-incomplete rebuilds only passports that are missing coverage, have no growth plan because
generation failed, or were built with an older VERIFIER_VERSION; the others are left untouched.

Prints one line per passport: username, requirements met before -> after, and the number of
rejected (unsupported) and excluded claims. A passport that fails is reported and left unchanged;
the others still run.
"""
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import VERIFIER_VERSION  # noqa: E402
from app.services.pipeline import recheck_passport  # noqa: E402
from app.services.storage import list_passports  # noqa: E402
from app.services.verifier import EXCLUDED_KINDS  # noqa: E402


def built_with(passport: dict[str, Any]) -> str:
    """The VERIFIER_VERSION a passport was built with ("" when it predates versioning)."""
    if passport.get("verifier_version"):
        return passport["verifier_version"]
    versions = [
        item["record"].get("verifier_version") or ""
        for skill in passport.get("skills", []) for item in skill.get("evidence", []) if item.get("record")
    ]
    return max(versions, default="")


def incomplete_reasons(passport: dict[str, Any]) -> list[str]:
    reasons = []
    if not passport.get("coverage"):
        reasons.append("no coverage")
    if not passport.get("growth_tasks") and passport.get("generation_error"):
        reasons.append("no growth plan")
    version = built_with(passport)
    if version < VERIFIER_VERSION:
        reasons.append(f"verifier {version or 'unversioned'}")
    return reasons


def summary_line(old: dict[str, Any], new: dict[str, Any]) -> str:
    rejected = new.get("rejected_claims", [])
    excluded = sum(1 for item in rejected if item.get("kind") in EXCLUDED_KINDS)
    total = new.get("role_match", {}).get("total", "?")
    return (
        f"{new['github_username']:<20} met {old.get('role_match', {}).get('met', '?')} -> "
        f"{new.get('role_match', {}).get('met', '?')} of {total}  "
        f"rejected {len(rejected) - excluded}  excluded {excluded}"
    )


def main(argv: list[str] | None = None) -> int:
    only_incomplete = "--only-incomplete" in (sys.argv[1:] if argv is None else argv)
    passports = list_passports()
    if only_incomplete:
        selected = [(old, incomplete_reasons(old)) for old in passports]
        selected = [(old, reasons) for old, reasons in selected if reasons]
        print(f"Re-checking {len(selected)} of {len(passports)} passport(s) (incomplete or older rules)...")
    else:
        selected = [(old, []) for old in passports]
        print(f"Re-checking {len(passports)} passport(s)...")
    failures = 0
    for old, reasons in selected:
        why = f"  [{', '.join(reasons)}]" if reasons else ""
        try:
            new = recheck_passport(old)
        except Exception as exc:  # report and continue with the next passport
            failures += 1
            print(f"{old.get('github_username', '?'):<20} FAILED: {exc.__class__.__name__}: {exc}{why}")
            continue
        line = summary_line(old, new)
        if new.get("generation_error"):
            line += f"  (growth plan not generated: {new['generation_error']})"
        print(line + why, flush=True)
    print(f"Done: {len(selected) - failures} rebuilt, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
