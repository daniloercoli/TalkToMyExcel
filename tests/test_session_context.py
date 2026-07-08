from __future__ import annotations

import pytest

from app.config import Config


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "change-me-now")
    monkeypatch.setattr(Config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(Config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(Config, "SETTINGS_FILE", tmp_path / "data" / "settings.json")
    monkeypatch.setattr(Config, "USERS_FILE", tmp_path / "data" / "users.json")

    import app.session as session_module

    monkeypatch.setattr(session_module, "SESSIONS_DIR", Config.DATA_DIR / "sessions")
    session_module.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    import app.app as app_module

    app = app_module.create_app()
    app.config["TESTING"] = True
    return app.test_client()


def login(client):
    response = client.post("/login", data={"email": "admin@example.com", "password": "change-me-now"})
    assert response.status_code == 302


def test_query_history_keeps_long_user_question(client):
    from app.session import get_history

    login(client)
    question = "Remember this long filter for the next question: " + " ".join(["serial-ABC"] * 80)
    assert len(question) > 500

    response = client.post("/api/query", json={"question": question})
    assert response.status_code == 200

    history = get_history("admin-example-com")
    assert history[0]["role"] == "user"
    assert history[0]["content"] == question

    context = client.get("/api/session/context").get_json()
    assert context["source"] == "history_estimate"
    assert context["history_chars"] >= len(question)
    assert context["chars"] >= context["history_chars"]
    assert context["estimated_tokens"] > 0


def test_session_context_uses_last_payload_and_clear_removes_it(client):
    from app.session import get_history, payload_path, save_history, save_payload_usage

    login(client)
    save_history("admin-example-com", [], {"role": "user", "content": "How many open cases?"}, "Two.")
    save_payload_usage(
        "admin-example-com",
        {"chars": 1234, "estimated_tokens": 309, "messages": 3, "source": "last_llm_payload"},
    )

    context = client.get("/api/session/context").get_json()
    assert context["chars"] == 1234
    assert context["estimated_tokens"] == 309
    assert context["payload_messages"] == 3
    assert context["source"] == "last_llm_payload"

    cleared = client.post("/api/session/clear")
    assert cleared.status_code == 200
    assert cleared.get_json() == {"ok": True}
    assert get_history("admin-example-com") == []
    assert not payload_path("admin-example-com").exists()
