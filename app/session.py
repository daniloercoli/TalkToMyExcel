from __future__ import annotations

import json
from pathlib import Path

from app.config import Config

SESSIONS_DIR = Config.DATA_DIR / "sessions"
MAX_HISTORY = 20
MAX_CHARS = 12_000
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def get_history(user_id: str) -> list[dict]:
    path = SESSIONS_DIR / f"{user_id}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(user_id: str, history: list[dict], message: dict, answer: str) -> list[dict]:
    history.append(message)
    history.append({"role": "assistant", "content": answer[:500]})
    data = []
    total = 0
    for m in reversed(history):
        truncated = {
            "role": m["role"],
            "content": m["content"][:500],
        }
        if total + len(truncated["content"]) <= MAX_CHARS:
            data.append(truncated)
            total += len(truncated["content"])
        if len(data) >= MAX_HISTORY:
            break
    path = SESSIONS_DIR / f"{user_id}.json"
    path.write_text(json.dumps(data[::-1], ensure_ascii=False), encoding="utf-8")
    return data[::-1]


def clear_history(user_id: str) -> None:
    path = SESSIONS_DIR / f"{user_id}.json"
    if path.exists():
        path.unlink()
