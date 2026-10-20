import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import json_cache


def test_json_cache_replaces_entry_atomically(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    path.write_text('{"old":true}', encoding="utf-8")

    json_cache.write_json_cache(path, {"name": "李明", "ready": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"name": "李明", "ready": True}
    assert list(tmp_path.iterdir()) == [path]


def test_json_cache_preserves_previous_entry_when_replace_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "cache.json"
    path.write_text('{"old":true}', encoding="utf-8")

    def fail_replace(_source: str, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(json_cache.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        json_cache.write_json_cache(path, {"new": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.iterdir()) == [path]


def test_json_cache_pruning_removes_expired_entries_and_bounds_size(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(json_cache, "MAX_CACHE_BYTES", 5)
    old = tmp_path / "old.json"
    old.write_text("12345", encoding="utf-8")
    over_budget = tmp_path / "over-budget.json"
    over_budget.write_text("12345", encoding="utf-8")
    recent = tmp_path / "recent.json"
    recent.write_text("12345", encoding="utf-8")
    expired_time = 1
    import os
    import time
    os.utime(old, (expired_time, expired_time))
    os.utime(over_budget, (time.time() - 2, time.time() - 2))
    os.utime(recent, (time.time() - 1, time.time() - 1))

    removed = json_cache.prune_json_cache(tmp_path)

    assert removed == 2
    assert not old.exists()
    assert not over_budget.exists()
    assert recent.exists()
