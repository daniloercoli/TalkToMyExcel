from __future__ import annotations

import pytest

from app.config import Config
from app.demo import cleanup_expired_demo_users
from app.stores import UserStore, workspace_for_user


@pytest.fixture
def demo_app(tmp_path, monkeypatch):
    configure_test_config(tmp_path, monkeypatch, demo_enabled=True)

    import app.app as app_module

    app = app_module.create_app()
    app.config["TESTING"] = True
    return app


def test_demo_feature_flag_off_hides_route_and_cta(tmp_path, monkeypatch):
    configure_test_config(tmp_path, monkeypatch, demo_enabled=False)

    import app.app as app_module

    app = app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    login = client.get("/login")
    assert login.status_code == 200
    assert b"Prova la demo" not in login.data
    assert client.post("/demo/start").status_code == 404


def configure_test_config(tmp_path, monkeypatch, *, demo_enabled: bool) -> None:
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "change-me-now")
    monkeypatch.setattr(Config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(Config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(Config, "SETTINGS_FILE", tmp_path / "data" / "settings.json")
    monkeypatch.setattr(Config, "USERS_FILE", tmp_path / "data" / "users.json")
    monkeypatch.setattr(Config, "DEMO_ENABLED", demo_enabled)
    monkeypatch.setattr(Config, "DEMO_TIMEOUT_MINUTES", 30)


def test_demo_start_creates_logged_in_temporary_user(demo_app):
    client = demo_app.test_client()

    response = client.post("/demo/start")
    assert response.status_code == 302

    with client.session_transaction() as session:
        user_id = session["user_id"]

    user = UserStore().get(user_id)
    assert user["is_demo"] is True
    assert user["email"].startswith("demo-")
    assert user["email"].endswith("@demo.local")

    home = client.get("/")
    assert home.status_code == 200
    assert b"Demo temporanea" in home.data

    settings = client.get("/settings")
    assert settings.status_code == 403


def test_admin_can_reset_workspace_data_after_confirmation(demo_app):
    client = demo_app.test_client()
    login = client.post("/login", data={"email": "admin@example.com", "password": "change-me-now"})
    assert login.status_code == 302

    settings = client.get("/settings")
    assert settings.status_code == 200
    assert b"Reset all workspace data" in settings.data

    metadata = Config.DATA_DIR / "workspaces" / "admin-example-com" / "workbook" / "metadata.json"
    upload = Config.UPLOAD_DIR / "workspaces" / "admin-example-com" / "staging" / "abc" / "file.csv"
    history = Config.DATA_DIR / "sessions" / "admin-example-com.json"
    for path in (metadata, upload, history):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("data", encoding="utf-8")

    no_confirm = client.post("/settings/reset-data")
    assert no_confirm.status_code == 400
    assert metadata.exists()
    assert upload.exists()
    assert history.exists()

    reset = client.post("/settings/reset-data", data={"confirm": "RESET"})
    assert reset.status_code == 302
    assert reset.headers["Location"].endswith("/settings?reset=1")
    assert not metadata.exists()
    assert not upload.exists()
    assert not history.exists()
    assert (Config.DATA_DIR / "workspaces").exists()
    assert (Config.UPLOAD_DIR / "workspaces").exists()
    assert (Config.DATA_DIR / "sessions").exists()
    assert Config.USERS_FILE.exists()
    assert Config.SETTINGS_FILE.exists()

    done = client.get("/settings?reset=1")
    assert b"Workspace data reset completed." in done.data


def test_demo_users_are_isolated(demo_app):
    first = demo_app.test_client()
    second = demo_app.test_client()

    first.post("/demo/start")
    second.post("/demo/start")

    with first.session_transaction() as session:
        first_user_id = session["user_id"]
    with second.session_transaction() as session:
        second_user_id = session["user_id"]

    assert first_user_id != second_user_id
    assert workspace_for_user(first_user_id).data_dir != workspace_for_user(second_user_id).data_dir


def test_expired_demo_session_is_deleted(demo_app):
    client = demo_app.test_client()
    client.post("/demo/start")
    with client.session_transaction() as session:
        user_id = session["user_id"]

    workspace = workspace_for_user(user_id)
    (workspace.data_dir / "marker.txt").write_text("demo data", encoding="utf-8")
    expire_user(user_id)

    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?demo_expired=1")
    assert UserStore().get(user_id) is None
    assert not workspace.data_dir.exists()
    assert not workspace.upload_dir.exists()


def test_cleanup_deletes_only_expired_demo_users(demo_app):
    store = UserStore()
    demo = store.create_demo()
    workspace = workspace_for_user(demo["id"])
    active_demo = store.create_demo()
    expire_user(demo["id"])

    deleted = cleanup_expired_demo_users(store)

    assert deleted == 1
    assert store.get(demo["id"]) is None
    assert not workspace.data_dir.exists()
    assert store.get(active_demo["id"]) is not None
    assert store.get("admin-example-com") is not None


def test_cleanup_script_runs(demo_app, capsys):
    from scripts import cleanup_demo_users

    assert cleanup_demo_users.main() == 0
    assert "Deleted 0 expired demo user(s)." in capsys.readouterr().out


def expire_user(user_id: str) -> None:
    store = UserStore()
    users = store.list()
    for user in users:
        if user["id"] == user_id:
            user["last_seen_at"] = "2000-01-01T00:00:00+00:00"
            user["updated_at"] = user["last_seen_at"]
    store.save(users)
