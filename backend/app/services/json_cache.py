"""Helpers for safely persisting JSON cache entries."""
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

MAX_CACHE_AGE_SECONDS = 30 * 24 * 60 * 60
MAX_CACHE_BYTES = 512 * 1024 * 1024
MAX_TEMP_AGE_SECONDS = 60 * 60


def prune_json_cache(directory: Path) -> int:
    """Remove old cache files and then oldest files until the directory is bounded."""
    now = time.time()
    removed = 0
    retained = []
    total_bytes = 0
    for path in directory.iterdir():
        try:
            stat = path.stat()
            if path.is_file() and path.name.startswith(".") and path.name.endswith(".tmp"):
                if now - stat.st_mtime > MAX_TEMP_AGE_SECONDS:
                    path.unlink(missing_ok=True)
                    removed += 1
                continue
            if not path.is_file() or not path.name.endswith(".json"):
                continue
            if now - stat.st_mtime > MAX_CACHE_AGE_SECONDS:
                path.unlink(missing_ok=True)
                removed += 1
                continue
            retained.append((stat.st_mtime, path, stat.st_size))
            total_bytes += stat.st_size
        except OSError:
            continue
    for _, path, size in sorted(retained):
        if total_bytes <= MAX_CACHE_BYTES:
            break
        try:
            path.unlink(missing_ok=True)
            total_bytes -= size
            removed += 1
        except OSError:
            continue
    return removed


def write_json_cache(path: Path, payload: Any) -> None:
    """Write a complete JSON value then atomically replace its cache entry."""
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            json.dump(payload, temporary_file, ensure_ascii=False)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
        prune_json_cache(path.parent)
    finally:
        if temporary_path is not None:
            Path(temporary_path).unlink(missing_ok=True)
