from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

from app.config import Config
from app.session import clear_history
from app.stores import UserStore, slug


def demo_timeout() -> timedelta:
    return timedelta(minutes=max(1, int(Config.DEMO_TIMEOUT_MINUTES)))


def is_demo_expired(user: dict, now_at: datetime | None = None) -> bool:
    if not user.get("is_demo"):
        return False
    last_seen = parse_time(user.get("last_seen_at") or user.get("updated_at") or user.get("created_at"))
    if not last_seen:
        return True
    return (now_at or datetime.now(timezone.utc)) - last_seen > demo_timeout()


def cleanup_expired_demo_users(store: UserStore | None = None) -> int:
    store = store or UserStore()
    deleted = 0
    for user in store.list():
        if is_demo_expired(user) and delete_demo_user(user["id"], store=store):
            deleted += 1
    return deleted


def delete_demo_user(user_id: str, store: UserStore | None = None) -> bool:
    store = store or UserStore()
    if not store.delete_if(user_id, is_demo_expired):
        return False
    workspace_id = slug(user_id)
    shutil.rmtree(Config.DATA_DIR / "workspaces" / workspace_id, ignore_errors=True)
    shutil.rmtree(Config.UPLOAD_DIR / "workspaces" / workspace_id, ignore_errors=True)
    clear_history(user_id)
    return True


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
