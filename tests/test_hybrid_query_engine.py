from __future__ import annotations

from app import query_engine
from app.routing import RoutePlan
from app.stores import Workspace


class FakeEmbedding:
    def encode_query(self, text):
        return [1.0]


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
        lambda _question, _metadata: 'SELECT row_id FROM "cases" WHERE lower("status") = \'open\'',
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
