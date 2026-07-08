from __future__ import annotations

import json
from pathlib import Path

from app.config import Config

SESSIONS_DIR = Config.DATA_DIR / "sessions"
MAX_HISTORY = 20
MAX_CHARS = 50_000
MAX_MESSAGE_CHARS = 2_500
PAYLOAD_OVERHEAD_CHARS = 500
CHARS_PER_TOKEN = 4
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def session_path(user_id: str) -> Path:
    return SESSIONS_DIR / f"{user_id}.json"


def payload_path(user_id: str) -> Path:
    return SESSIONS_DIR / f"{user_id}.payload.json"


def get_history(user_id: str) -> list[dict]:
    path = session_path(user_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(user_id: str, history: list[dict], message: dict, answer: str) -> list[dict]:
    history.append(message)
    history.append({"role": "assistant", "content": answer[:MAX_MESSAGE_CHARS]})
    data = []
    total = 0
    for m in reversed(history):
        truncated = {
            "role": m["role"],
            "content": m["content"][:MAX_MESSAGE_CHARS],
        }
        if total + len(truncated["content"]) <= MAX_CHARS:
            data.append(truncated)
            total += len(truncated["content"])
        if len(data) >= MAX_HISTORY:
            break
    path = session_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data[::-1], ensure_ascii=False), encoding="utf-8")
    return data[::-1]


def save_payload_usage(user_id: str, usage: dict | None) -> None:
    if not usage:
        return
    path = payload_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(usage, ensure_ascii=False), encoding="utf-8")


def get_payload_usage(user_id: str) -> dict | None:
    path = payload_path(user_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("chars"), int) else None


def estimate_history_payload(history: list[dict]) -> dict:
    recent = history[-MAX_HISTORY:]
    chars = PAYLOAD_OVERHEAD_CHARS + sum(
        len(str(m.get("role", ""))) + len(str(m.get("content", ""))) for m in recent
    )
    return {
        "chars": chars,
        "estimated_tokens": round(chars / CHARS_PER_TOKEN),
        "messages": len(recent) + 2,
        "source": "history_estimate",
    }


def clear_history(user_id: str) -> None:
    for path in (session_path(user_id), payload_path(user_id)):
        if path.exists():
            path.unlink()
