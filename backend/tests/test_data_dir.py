import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PRINT_PATHS = (
    "import json; from app import config; "
    "print(json.dumps({'data': str(config.DATA_DIR), 'db': str(config.DATABASE_PATH), "
    "'cache': str(config.CACHE_DIR)}))"
)


def _config_paths(data_dir: str | None) -> dict[str, str]:
    # A fresh interpreter, so importing config here can't change paths other tests already use.
    env = {key: value for key, value in os.environ.items() if key != "DATA_DIR"}
    env["APP_ENV"] = "development"
    if data_dir is not None:
        env["DATA_DIR"] = data_dir
    result = subprocess.run(
        [sys.executable, "-c", PRINT_PATHS],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_data_dir_defaults_to_backend_folder() -> None:
    paths = _config_paths(None)
    assert Path(paths["data"]) == BACKEND_DIR
    assert Path(paths["db"]) == BACKEND_DIR / "skill_passport.db"
    assert Path(paths["cache"]) == BACKEND_DIR / "cache"


def test_data_dir_moves_database_and_cache_and_is_created(tmp_path) -> None:
    target = tmp_path / "disk" / "data"
    paths = _config_paths(str(target))
    assert Path(paths["db"]) == target / "skill_passport.db"
    assert Path(paths["cache"]) == target / "cache"
    assert (target / "cache").is_dir()
