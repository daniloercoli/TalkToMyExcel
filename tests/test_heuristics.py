from app import query_engine
from app.query_engine import classify, query_tokens
from app.workbook import table_name


def test_table_name_is_stable_and_safe():
    assert table_name("After Sales Cases") == "sheet_after_sales_cases"
    assert table_name("  ") == "sheet_data"


def test_classify_routes_counts_before_semantic():
    assert classify("How many open cases do we have?") == "count"
    assert classify("What is the status for matricola ABC123?") == "status"
    assert classify("Find cases similar to motor vibration") == "semantic"
    assert classify("Compare the numeric columns and find outliers") == "python"
    assert classify("Calcola il rapporto tra costo garanzia e importo") == "python"


def test_query_tokens_keeps_likely_identifiers():
    assert "ABC-123" in query_tokens("What is the status for serial ABC-123?")


def test_plan_route_can_promote_ambiguous_questions_to_python(monkeypatch):
    class Planner:
        def generate(self, system, user, model, temperature=0.2):
            return '{"route": "python", "reason": "needs dataframe operations"}'

    monkeypatch.setattr(query_engine, "load_settings", lambda: {})
    monkeypatch.setattr(query_engine, "get_llm_provider", lambda _settings=None: (Planner(), "fake"))

    plan = query_engine.plan_route("Find records that require a multi-step calculation", {"tables": []})

    assert plan["route"] == "python"
