from __future__ import annotations

import json

import pytest

from app.config import Config
from app.stores import Workspace


class RewriterLLM:
    def __init__(self, rewritten: str):
        self.rewritten = rewritten

    def generate(self, system, user, model, temperature=0.2, messages=None):
        if "Rewrite the latest user question" in system:
            return json.dumps({"question": self.rewritten})
        return "ok"


class BrokenRewriteLLM:
    def generate(self, system, user, model, temperature=0.2, messages=None):
        return "not json"


def metadata():
    return {
        "datasets": [],
        "tables": [
            {
                "table": "cases",
                "sheet": "Cases",
                "filename": "cases.xlsx",
                "columns": ["status", "problem_description", "priority"],
            }
        ],
    }


def test_answer_question_rewrites_follow_up_before_routing(monkeypatch, tmp_path):
    from app import query_engine

    workspace = Workspace("user", "workspace", tmp_path / "data", tmp_path / "uploads")
    rewritten = "Find open cases similar to motor vibration"
    captured = {}

    monkeypatch.setattr(query_engine, "active_workbook", lambda _workspace: metadata())
    monkeypatch.setattr(query_engine, "get_llm_provider", lambda _settings=None: (RewriterLLM(rewritten), "fake"))

    def fake_run_route(workspace, metadata, question, route, route_plan, request_id, conversation_history):
        captured["question"] = question
        captured["route"] = route
        return {"answer": "ok", "route": route, "sources": [], "debug": {}}

    monkeypatch.setattr(query_engine, "run_route", fake_run_route)

    result = query_engine.answer_question(
        workspace,
        "same, but only open",
        conversation_history=[
            {"role": "user", "content": "Find cases similar to motor vibration"},
            {"role": "assistant", "content": "The closest vibration cases are MX-1001 and MX-1003."},
        ],
    )

    assert captured["question"] == rewritten
    assert captured["route"] == "hybrid"
    assert result["route"] == "hybrid"
    assert result["debug"]["question_context"]["effective"] == rewritten
    assert result["debug"]["question_context"]["changed"] is True


def test_resolve_question_keeps_original_when_rewrite_fails(monkeypatch):
    from app import query_engine

    monkeypatch.setattr(query_engine, "get_llm_provider", lambda _settings=None: (BrokenRewriteLLM(), "fake"))

    question, context = query_engine.resolve_question(
        "same, but only open",
        metadata(),
        conversation_history=[{"role": "user", "content": "Find cases similar to motor vibration"}],
    )

    assert question == "same, but only open"
    assert context["changed"] is False
    assert context["source"] == "rewrite_failed"


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

    import app.app as app_module
    import app.session as session_module

    monkeypatch.setattr(session_module, "SESSIONS_DIR", Config.DATA_DIR / "sessions")
    session_module.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        app_module,
        "answer_question",
        lambda _workspace, _question, request_id="", conversation_history=None: {
            "answer": "ok",
            "route": "hybrid",
            "sources": [],
            "debug": {
                "question_context": {
                    "changed": True,
                    "source": "llm_rewrite",
                    "original": _question,
                    "effective": "Find open cases similar to motor vibration",
                },
                "llm_payload": {
                    "chars": 100,
                    "estimated_tokens": 25,
                    "messages": 3,
                    "source": "last_llm_payload",
                },
            },
        },
    )

    app = app_module.create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_api_query_saves_resolved_question_for_future_context(client):
    from app.session import get_history

    login = client.post("/login", data={"email": "admin@example.com", "password": "change-me-now"})
    assert login.status_code == 302

    response = client.post("/api/query", json={"question": "same, but only open"})
    assert response.status_code == 200

    history = get_history("admin-example-com")
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Find open cases similar to motor vibration"
