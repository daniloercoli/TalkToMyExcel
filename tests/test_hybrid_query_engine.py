from __future__ import annotations

import json

from app import query_engine
from app.routing import RoutePlan
from app.stores import Workspace


class FakeEmbedding:
    def encode_query(self, text):
        return [1.0]


class CapturingEmbedding:
    def __init__(self):
        self.queries = []

    def encode_query(self, text):
        self.queries.append(text)
        return [1.0]


class CapturingLLM:
    def __init__(self):
        self.users = []

    def generate(self, system, user, model, temperature=0.2):
        self.users.append(user)
        return json.dumps({"sql": 'SELECT count(*) FROM "cases"'})


def make_workspace(tmp_path):
    import duckdb

    workspace = Workspace(
        user_id="user",
        workspace_id="workspace",
        data_dir=tmp_path / "data",
        upload_dir=tmp_path / "uploads",
    )
    workspace.workbook_dir.mkdir(parents=True)
    conn = duckdb.connect(str(workspace.duckdb_path))
    conn.execute(
        """
        CREATE TABLE "cases" (
            row_id VARCHAR,
            workbook_id VARCHAR,
            workbook_filename VARCHAR,
            sheet_name VARCHAR,
            original_row_number INTEGER,
            matricola VARCHAR,
            status VARCHAR,
            problem_description VARCHAR
        )
        """
    )
    conn.execute(
        """
        INSERT INTO "cases" VALUES
            ('cases_1', 'wb', 'cases.xlsx', 'Cases', 1, 'MX-1001', 'open', 'Motor vibration'),
            ('cases_2', 'wb', 'cases.xlsx', 'Cases', 2, 'MX-1002', 'closed', 'Hydraulic leak')
        """
    )
    conn.close()
    metadata = {
        "datasets": [],
        "tables": [
            {
                "table": "cases",
                "sheet": "Cases",
                "filename": "cases.xlsx",
                "workbook_id": "wb",
                "columns": ["matricola", "status", "problem_description"],
                "semantic_columns": ["problem_description"],
            }
        ],
    }
    return workspace, metadata


def test_hybrid_semantic_context_filters_vectors_with_sql_row_ids(monkeypatch, tmp_path):
    workspace, metadata = make_workspace(tmp_path)

    monkeypatch.setattr(
        query_engine,
        "generate_hybrid_filter_sql",
        lambda _question, _metadata, _history=None: 'SELECT row_id FROM "cases" WHERE lower("status") = \'open\'',
    )
    monkeypatch.setattr(query_engine, "get_embedding_provider", lambda _settings: (FakeEmbedding(), "fake"))

    def fake_query_rows(path, collection_name, embedding, top_k=20, row_ids=None):
        assert set(row_ids) == {"cases_1"}
        return [{"id": "cases_1", "distance": 0.01}]

    monkeypatch.setattr(query_engine, "query_rows", fake_query_rows)

    context = query_engine.hybrid_semantic_context(workspace, metadata, "Find open cases similar to vibration")

    assert context["kind"] == "hybrid"
    assert context["rows"][0]["matricola"] == "MX-1001"
    assert context["debug"]["filtered_rows"] == 1
    assert context["debug"]["semantic_hits"] == 1


def test_sql_generators_include_recent_conversation_context(monkeypatch, tmp_path):
    _workspace, metadata = make_workspace(tmp_path)
    llm = CapturingLLM()
    history = [
        {"role": "user", "content": "Find cases similar to motor vibration"},
        {"role": "assistant", "content": "The closest vibration cases are MX-1001 and MX-1003."},
    ]

    monkeypatch.setattr(query_engine, "load_settings", lambda: {})
    monkeypatch.setattr(query_engine, "get_llm_provider", lambda _settings: (llm, "fake"))

    query_engine.generate_sql_query("same, but only open", metadata, history)
    query_engine.generate_hybrid_filter_sql("same, but only open", metadata, history)

    assert len(llm.users) == 2
    for user in llm.users:
        assert "Recent context" in user
        assert "motor vibration" in user
        assert "Question:\nsame, but only open" in user


def test_answer_question_routes_followup_with_recent_context(monkeypatch, tmp_path):
    workspace, metadata = make_workspace(tmp_path)
    captured = {}
    history = [
        {"role": "user", "content": "Find cases similar to motor vibration"},
        {"role": "assistant", "content": "The closest vibration cases are MX-1001 and MX-1003."},
    ]

    monkeypatch.setattr(query_engine, "active_workbook", lambda _workspace: metadata)

    def fake_run_route(workspace, metadata, question, route, route_plan, request_id, conversation_history):
        captured["question"] = question
        captured["route"] = route
        return {"answer": "ok", "route": route, "sources": [], "debug": {}}

    monkeypatch.setattr(query_engine, "run_route", fake_run_route)

    result = query_engine.answer_question(workspace, "same, but only open", conversation_history=history)

    assert captured["question"] == "same, but only open"
    assert captured["route"] == "hybrid"
    assert result["debug"]["route_plan"]["source"] == "HybridStructuredSemanticStrategy"


def test_detail_followup_keeps_python_route(monkeypatch, tmp_path):
    workspace, metadata = make_workspace(tmp_path)
    captured = {}
    history = [
        {"role": "user", "content": "Find open cases similar to motor vibration"},
        {"role": "assistant", "content": "The closest cases are MX-1001 and MX-1003."},
    ]

    monkeypatch.setattr(query_engine, "active_workbook", lambda _workspace: metadata)

    def fake_run_route(workspace, metadata, question, route, route_plan, request_id, conversation_history):
        captured["route"] = route
        return {"answer": "ok", "route": route, "sources": [], "debug": {}}

    monkeypatch.setattr(query_engine, "run_route", fake_run_route)

    query_engine.answer_question(workspace, "stampa i dettagli di quelle 2 richieste", conversation_history=history)

    assert captured["route"] == "python"


def test_semantic_context_embeds_followup_with_recent_context(monkeypatch, tmp_path):
    workspace, metadata = make_workspace(tmp_path)
    embedder = CapturingEmbedding()
    history = [
        {"role": "user", "content": "Find cases similar to motor vibration"},
        {"role": "assistant", "content": "The closest vibration cases are MX-1001 and MX-1003."},
    ]

    monkeypatch.setattr(query_engine, "get_embedding_provider", lambda _settings: (embedder, "fake"))
    monkeypatch.setattr(query_engine, "query_rows", lambda *args, **kwargs: [{"id": "cases_1", "distance": 0.01}])

    context = query_engine.semantic_context(workspace, metadata, "same", conversation_history=history)

    assert "motor vibration" in embedder.queries[0]
    assert "Follow-up question:\nsame" in embedder.queries[0]
    assert context["debug"]["retrieval_contextualized"] is True


def test_multi_answer_synthesizes_successful_subroutes(monkeypatch):
    def fake_run_route(workspace, metadata, question, route, route_plan, request_id, conversation_history):
        if route == "sql":
            return {"answer": "There are 2 open cases.", "route": "sql", "sources": [], "debug": {"rows": 1}}
        if route == "semantic":
            return {
                "answer": "MX-1001 has vibration notes.",
                "route": "semantic",
                "sources": [{"row_id": "cases_1", "file": "cases.xlsx", "sheet": "Cases", "row": 1}],
                "debug": {"semantic_hits": 1},
            }
        raise AssertionError(f"unexpected route: {route}")

    def fake_llm_answer(question, context, route, conversation_history):
        assert route == "multi"
        assert [row["route"] for row in context["rows"]] == ["sql", "semantic"]
        return {
            "answer": "There are 2 open cases; MX-1001 has vibration notes.",
            "route": route,
            "sources": context["sources"],
            "debug": context["debug"],
        }

    monkeypatch.setattr(query_engine, "run_route", fake_run_route)
    monkeypatch.setattr(query_engine, "llm_answer", fake_llm_answer)

    plan = RoutePlan(route="multi", reason="multi_intent", candidates=("sql", "semantic"), execution="multi")
    result = query_engine.multi_answer(None, {}, "How many open cases and which notes mention vibration?", plan, "", None)

    assert result["route"] == "multi"
    assert result["sources"] == [{"row_id": "cases_1", "file": "cases.xlsx", "sheet": "Cases", "row": 1}]
    assert result["debug"]["multi_routes"] == ["sql", "semantic"]
