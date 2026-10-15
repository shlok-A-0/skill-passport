import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@lru_cache(maxsize=1)
def get_skills() -> list[dict[str, Any]]:
    return json.loads((DATA_DIR / "skills.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def get_roles() -> list[dict[str, Any]]:
    return json.loads((DATA_DIR / "roles.json").read_text(encoding="utf-8"))


def skill_map() -> dict[str, dict[str, Any]]:
    return {skill["id"]: skill for skill in get_skills()}
