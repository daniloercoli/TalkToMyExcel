from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from app.config import Config
from app.json_store import read_json, update_json
from app.providers.factory import load_settings, save_settings


def _increment(path: str, times: int) -> None:
    counter_path = Path(path)
    for _ in range(times):
        update_json(
            counter_path,
            {"count": 0},
            lambda current: {"count": current["count"] + 1},
        )


def test_update_json_serializes_processes(tmp_path):
    path = tmp_path / "counter.json"
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_increment, args=(str(path), 10)) for _ in range(4)]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)

    assert [process.exitcode for process in processes] == [0, 0, 0, 0]
    assert read_json(path, {}) == {"count": 40}
    assert not list(tmp_path.glob("*.tmp"))


def test_provider_settings_round_trip_uses_json_store(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "SETTINGS_FILE", tmp_path / "settings.json")
    settings = load_settings()
    settings["chat"]["model"] = "custom-model"

    save_settings(settings)

    assert load_settings()["chat"]["model"] == "custom-model"
    assert not list(tmp_path.glob("*.tmp"))


def test_session_save_merges_stale_histories(tmp_path, monkeypatch):
    import app.session as session

    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path / "sessions")
    stale_first = session.get_history("user")
    stale_second = session.get_history("user")

    session.save_history("user", stale_first, {"role": "user", "content": "first"}, "one")
    session.save_history("user", stale_second, {"role": "user", "content": "second"}, "two")

    assert [item["content"] for item in session.get_history("user")] == ["first", "one", "second", "two"]


def test_session_save_repairs_corrupt_history(tmp_path, monkeypatch):
    import app.session as session

    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path / "sessions")
    path = session.session_path("user")
    path.parent.mkdir(parents=True)
    path.write_text("{invalid", encoding="utf-8")

    session.save_history("user", [], {"role": "user", "content": "question"}, "answer")

    assert [item["content"] for item in session.get_history("user")] == ["question", "answer"]


def test_update_json_rejects_corrupt_json_by_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        update_json(path, {}, lambda current: current)

    assert path.read_text(encoding="utf-8") == "{invalid"
