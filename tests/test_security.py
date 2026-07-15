from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

from app.config import Config, ensure_dirs
from app.providers.openai_compatible import OpenAICompatibleEmbedding, OpenAICompatibleLLM
from app.stores import UserStore, workspace_for_user


def test_gunicorn_forces_production_validation():
    config = runpy.run_path(str(Path(__file__).parents[1] / "gunicorn.conf.py"))

    assert "APP_ENV=production" in config["raw_env"]


def test_openai_compatible_clients_use_default_tls_verification(monkeypatch):
    clients = []

    def fake_openai(**kwargs):
        clients.append(kwargs)
        return object()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=fake_openai))
    provider = {"base_url": "https://provider.example/v1", "requires_api_key": False}

    OpenAICompatibleLLM(provider)
    OpenAICompatibleEmbedding(provider, "embedding-model")

    assert len(clients) == 2
    assert all("http_client" not in client for client in clients)


@pytest.mark.parametrize("secret", ["", "change-this-secret", "dev-only-change-me"])
def test_production_rejects_placeholder_secret(tmp_path, monkeypatch, secret):
    monkeypatch.setattr(Config, "ENVIRONMENT", "production")
    monkeypatch.setattr(Config, "SECRET_KEY", secret)
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(Config, "LOG_DIR", tmp_path / "logs")

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        ensure_dirs()

    assert not Config.DATA_DIR.exists()


def test_development_allows_placeholder_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "ENVIRONMENT", "development")
    monkeypatch.setattr(Config, "SECRET_KEY", "dev-only-change-me")
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(Config, "LOG_DIR", tmp_path / "logs")

    ensure_dirs()

    assert Config.DATA_DIR.is_dir()


def test_production_rejects_placeholder_admin_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "ENVIRONMENT", "production")
    monkeypatch.setenv("ADMIN_PASSWORD", "change-me-now")
    store = UserStore(tmp_path / "users.json")

    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        store.bootstrap_admin()

    assert store.list() == []


def test_production_rejects_existing_admin_with_placeholder_password(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "ENVIRONMENT", "production")
    store = UserStore(tmp_path / "users.json")
    store.save(
        [
            {
                "id": "legacy-admin",
                "email": "admin@example.com",
                "password_hash": generate_password_hash("change-me-now"),
                "role": "admin",
            }
        ]
    )

    with pytest.raises(RuntimeError, match="placeholder password"):
        store.bootstrap_admin()


def test_production_rejects_existing_admin_with_empty_password(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "ENVIRONMENT", "production")
    store = UserStore(tmp_path / "users.json")
    store.save(
        [
            {
                "id": "legacy-admin",
                "email": "admin@example.com",
                "password_hash": generate_password_hash(""),
                "role": "admin",
            }
        ]
    )

    with pytest.raises(RuntimeError, match="placeholder password"):
        store.bootstrap_admin()


@pytest.mark.parametrize("password", ["", "change-me-now"])
def test_production_rejects_new_admin_with_placeholder_password(tmp_path, monkeypatch, password):
    monkeypatch.setattr(Config, "ENVIRONMENT", "production")
    store = UserStore(tmp_path / "users.json")

    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        store.create("second-admin@example.com", password, role="admin")

    assert store.list() == []


def test_production_bootstraps_with_secure_values(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "ENVIRONMENT", "production")
    monkeypatch.setenv("ADMIN_PASSWORD", "a-unique-production-password")
    store = UserStore(tmp_path / "users.json")

    store.bootstrap_admin()

    assert store.authenticate("admin@example.com", "a-unique-production-password")["role"] == "admin"


def test_new_user_ids_are_opaque_and_retry_a_collision(tmp_path, monkeypatch):
    generated = iter(["a" * 32, "a" * 32, "b" * 32])
    monkeypatch.setattr("app.stores.secrets.token_hex", lambda _size: next(generated))
    store = UserStore(tmp_path / "users.json")

    first = store.create("a+b@example.com", "secret")
    second = store.create("a-b@example.com", "secret")

    assert first["id"] == "a" * 32
    assert second["id"] == "b" * 32
    assert first["id"] != second["id"]
    assert first["email"] not in first["id"]


def test_duplicate_legacy_user_ids_fail_closed(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text(
        json.dumps(
            {
                "users": [
                    {"id": "shared-id", "email": "first@example.com"},
                    {"id": "shared-id", "email": "second@example.com"},
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Duplicate user IDs"):
        UserStore(users_path).list()


def test_legacy_user_id_and_workspace_path_remain_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    store = UserStore(tmp_path / "users.json")
    store.save(
        [
            {
                "id": "legacy-user-example-com",
                "email": "legacy.user@example.com",
                "password_hash": generate_password_hash("secret"),
                "role": "user",
            }
        ]
    )

    user = store.authenticate("legacy.user@example.com", "secret")
    workspace = workspace_for_user(user["id"])

    assert user["id"] == "legacy-user-example-com"
    assert workspace.data_dir == Config.DATA_DIR / "workspaces" / "legacy-user-example-com"
