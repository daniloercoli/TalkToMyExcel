from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from app.config import Config


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.lower()).strip("-")
    return safe[:64] or secrets.token_hex(8)


class UserStore:
    def __init__(self, path: Path | None = None):
        self.path = path or Config.USERS_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def bootstrap_admin(self) -> None:
        if self.list():
            return
        email = os.getenv("ADMIN_EMAIL", "admin@example.com")
        password = os.getenv("ADMIN_PASSWORD", "change-me-now")
        self.create(email=email, password=password, role="admin", display_name="Admin")

    def list(self) -> list[dict]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return data.get("users", [])

    def save(self, users: list[dict]) -> None:
        self.path.write_text(json.dumps({"users": users}, indent=2, sort_keys=True), encoding="utf-8")

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
        users = self.list()
        if any(user["email"] == email for user in users):
            raise ValueError("User already exists")
        created_at = now()
        user = {
            "id": slug(email),
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
        users.append(user)
        self.save(users)
        return public_user(user)

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
        users = self.list()
        timestamp = now()
        for user in users:
            if user["id"] == user_id:
                user["updated_at"] = timestamp
                user["last_seen_at"] = timestamp
                self.save(users)
                return public_user(user)
        return None

    def delete(self, user_id: str) -> bool:
        users = self.list()
        kept = [user for user in users if user["id"] != user_id]
        if len(kept) == len(users):
            return False
        self.save(kept)
        return True

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
