from __future__ import annotations

import os
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from app.config import Config, is_production, validate_production_admin_password
from app.json_store import read_json, update_json, write_json


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.lower()).strip("-")
    return safe[:64] or secrets.token_hex(8)


def _users_from_document(document: dict) -> list[dict]:
    users = document.get("users", [])
    user_ids = [user.get("id") for user in users]
    if len(user_ids) != len(set(user_ids)):
        raise RuntimeError("Duplicate user IDs detected; resolve them before starting the application")
    return users


def _new_user(
    users: list[dict],
    email: str,
    password: str,
    role: str,
    display_name: str,
    *,
    is_demo: bool = False,
) -> dict:
    created_at = now()
    existing_ids = {user.get("id") for user in users}
    user_id = secrets.token_hex(16)
    while user_id in existing_ids:
        user_id = secrets.token_hex(16)
    user = {
        "id": user_id,
        "email": email,
        "display_name": display_name.strip() or email,
        "password_hash": generate_password_hash(password),
        "role": role if role in {"admin", "user"} else "user",
        "enabled": True,
        "created_at": created_at,
        "updated_at": created_at,
    }
    if is_demo:
        user["is_demo"] = True
        user["last_seen_at"] = created_at
    return user


class UserStore:
    def __init__(self, path: Path | None = None):
        self.path = path or Config.USERS_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def bootstrap_admin(self) -> None:
        email = os.getenv("ADMIN_EMAIL", "admin@example.com")
        password = os.getenv("ADMIN_PASSWORD", "change-me-now")

        def bootstrap(document: dict) -> dict:
            users = _users_from_document(document)
            if users:
                if is_production() and any(
                    user.get("role") == "admin"
                    and any(
                        check_password_hash(user.get("password_hash", ""), placeholder)
                        for placeholder in ("", "change-me-now")
                    )
                    for user in users
                ):
                    raise RuntimeError("Replace the placeholder password for every production admin")
                return document
            validate_production_admin_password(password)
            admin = _new_user(users, email.strip().lower(), password, "admin", "Admin")
            return {**document, "users": [admin]}

        update_json(self.path, {"users": []}, bootstrap, indent=2, sort_keys=True)

    def list(self) -> list[dict]:
        return _users_from_document(read_json(self.path, {"users": []}))

    def save(self, users: list[dict]) -> None:
        _users_from_document({"users": users})
        write_json(self.path, {"users": users}, indent=2, sort_keys=True)

    def create(
        self,
        email: str,
        password: str,
        role: str = "user",
        display_name: str = "",
        *,
        is_demo: bool = False,
    ) -> dict:
        email = email.strip().lower()
        if role == "admin":
            validate_production_admin_password(password)
        created = None

        def add_user(document: dict) -> dict:
            nonlocal created
            users = _users_from_document(document)
            if any(user["email"] == email for user in users):
                raise ValueError("User already exists")
            created = _new_user(users, email, password, role, display_name, is_demo=is_demo)
            return {**document, "users": [*users, created]}

        update_json(self.path, {"users": []}, add_user, indent=2, sort_keys=True)
        return public_user(created)

    def create_demo(self) -> dict:
        for _attempt in range(5):
            token = secrets.token_hex(4)
            try:
                return self.create(
                    email=f"demo-{token}@demo.local",
                    password=secrets.token_urlsafe(24),
                    display_name="Demo session",
                    is_demo=True,
                )
            except ValueError:
                continue
        raise ValueError("Could not create demo user")

    def touch(self, user_id: str) -> dict | None:
        touched = None

        def touch_user(document: dict) -> dict:
            nonlocal touched
            users = _users_from_document(document)
            for user in users:
                if user["id"] == user_id:
                    timestamp = now()
                    user["updated_at"] = timestamp
                    user["last_seen_at"] = timestamp
                    touched = public_user(user)
                    break
            return {**document, "users": users}

        update_json(self.path, {"users": []}, touch_user, indent=2, sort_keys=True)
        return touched

    def delete_if(self, user_id: str, predicate: Callable[[dict], bool]) -> bool:
        deleted = False

        def delete_user(document: dict) -> dict:
            nonlocal deleted
            users = _users_from_document(document)
            kept = []
            for user in users:
                if user["id"] == user_id and predicate(public_user(user)):
                    deleted = True
                else:
                    kept.append(user)
            return {**document, "users": kept}

        update_json(self.path, {"users": []}, delete_user, indent=2, sort_keys=True)
        return deleted

    def delete(self, user_id: str) -> bool:
        return self.delete_if(user_id, lambda _user: True)

    def authenticate(self, email: str, password: str) -> dict | None:
        email = email.strip().lower()
        for user in self.list():
            if user["email"] == email and user.get("enabled", True):
                if check_password_hash(user["password_hash"], password):
                    return public_user(user)
        return None

    def get(self, user_id: str) -> dict | None:
        for user in self.list():
            if user["id"] == user_id:
                return public_user(user)
        return None


def public_user(user: dict) -> dict:
    return {key: value for key, value in user.items() if key != "password_hash"}


@dataclass
class Workspace:
    user_id: str
    workspace_id: str
    data_dir: Path
    upload_dir: Path

    @property
    def staging_dir(self) -> Path:
        return self.upload_dir / "staging"

    @property
    def workbook_dir(self) -> Path:
        return self.data_dir / "workbook"

    @property
    def duckdb_path(self) -> Path:
        return self.workbook_dir / "data.duckdb"

    @property
    def metadata_path(self) -> Path:
        return self.workbook_dir / "metadata.json"

    @property
    def chroma_dir(self) -> Path:
        return self.workbook_dir / "chroma"

    @property
    def chroma_collection(self) -> str:
        return f"workbook_{self.workspace_id.replace('-', '_')}"


def workspace_for_user(user_id: str) -> Workspace:
    workspace_id = slug(user_id)
    data_dir = Config.DATA_DIR / "workspaces" / workspace_id
    upload_dir = Config.UPLOAD_DIR / "workspaces" / workspace_id
    data_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return Workspace(user_id=user_id, workspace_id=workspace_id, data_dir=data_dir, upload_dir=upload_dir)
