"""
Tests for the LLM-based routing system.

The new system uses:
1. Fast heuristics for obvious cases (explicit python, details, status+id)
2. LLM for intelligent routing of all complex cases
3. Simple fallbacks if LLM fails
"""

import pytest
from app.routing import (
    QueryRouter,
    RoutePlan,
    ExplicitPythonStrategy,
    DetailRequestStrategy,
    ExplicitGroupBySQLStrategy,
    HybridStructuredSemanticStrategy,
    StatusIdStrategy,
    SQLRouteStrategy,
    MultiColumnCountStrategy,
    MultiRouteStrategy,
    LLMRouterStrategy,
    route_tool_prompt,
)


class TestExplicitPythonStrategy:
    """Fast path for explicit Python requests"""

    def test_uses_python_phrase(self):
        strategy = ExplicitPythonStrategy()
        assert strategy.matches("usa python per contare") is True
        assert strategy.plan("usa python", {}) == RoutePlan(route="python", reason="user_explicit")

    def test_use_python_english(self):
        strategy = ExplicitPythonStrategy()
        assert strategy.matches("use python to filter") is True

    def test_devii_usare_python(self):
        strategy = ExplicitPythonStrategy()
        assert strategy.matches("devi usare python") is True

    def test_csv_reference(self):
        strategy = ExplicitPythonStrategy()
        assert strategy.matches("leggi dal csv") is True


class TestDetailRequestStrategy:
    """Fast path for detail/row listing requests"""

    def test_stampa_dettagli(self):
        strategy = DetailRequestStrategy()
        assert strategy.matches("stampa i dettagli") is True
        assert strategy.plan("stampa dettagli", {}) == RoutePlan(route="sql", reason="detail_request")

    def test_mostra_righe(self):
        strategy = DetailRequestStrategy()
        assert strategy.matches("mostra le righe") is True

    def test_show_details(self):
        strategy = DetailRequestStrategy()
        assert strategy.matches("show details of these rows") is True

    def test_list_all(self):
        strategy = DetailRequestStrategy()
        assert strategy.matches("list all matching rows") is True

    def test_plain_note_listing_is_a_detail_request(self):
        assert QueryRouter().plan("Mostra le note", {}).route == "sql"


class TestStatusIdStrategy:
    """Fast path for status lookup by ID"""

    def test_status_and_serial(self):
        strategy = StatusIdStrategy()
        assert strategy.matches("qual e lo stato della matricola 123") is True
        assert strategy.plan("stato matricola", {}) == RoutePlan(route="status", reason="status_by_id")

    def test_state_and_asset(self):
        strategy = StatusIdStrategy()
        assert strategy.matches("what is the state of asset ABC") is True

    def test_no_serial_no_match(self):
        strategy = StatusIdStrategy()
        assert strategy.matches("qual e lo stato") is False


class TestSQLRouteStrategy:
    """Fast path for SQL-capable queries"""

    def test_count_with_filter(self):
        strategy = SQLRouteStrategy()
        assert strategy.matches("quanti abbiamo con stato = closed") is True
        assert strategy.plan("quanti con closed", {}) == RoutePlan(route="sql", reason="sql_capable")

    def test_filter_request(self):
        strategy = SQLRouteStrategy()
        assert strategy.matches("filtra solo per le macchine WIP") is True

    def test_aggregate_request(self):
        strategy = SQLRouteStrategy()
        assert strategy.matches("qual è la media degli interventi") is True

    def test_groupby_is_sql_capable(self):
        strategy = SQLRouteStrategy()
        # The router's more specific grouped-count strategy wins first.
        # The strategy itself matches if it's SQL-like.
        assert strategy.matches("quanti per priorita") is True 
        # Note: The Router's list order decides who wins.


class TestLLMRouterStrategy:
    """LLM handles all complex routing decisions"""

    def test_llm_always_matches(self):
        strategy = LLMRouterStrategy()
        assert strategy.matches("anything goes here") is True

    def test_route_tool_prompt_describes_hybrid_and_multi(self):
        prompt = route_tool_prompt()
        assert "`hybrid`" in prompt
        assert "`multi`" in prompt
        assert "After-sales examples" in prompt


class TestHybridStructuredSemanticStrategy:
    """Fast path for SQL + vector questions"""

    def test_structured_filter_and_similarity(self):
        strategy = HybridStructuredSemanticStrategy()
        assert strategy.matches("Find open cases similar to motor vibration") is True
        assert strategy.plan("Find open cases similar to motor vibration", {}).route == "hybrid"

    def test_structured_filter_and_mentions(self):
        strategy = HybridStructuredSemanticStrategy()
        assert strategy.matches('Tra le richieste con "STATO" = "NEW", quali citano "macchina"?') is True


class TestExplicitGroupBySQLStrategy:
    """Explicit column distributions should use SQL GROUP BY"""

    def test_per_ciascun_valore(self):
        strategy = ExplicitGroupBySQLStrategy()
        assert strategy.matches('Quante richieste ci sono per ciascun valore di "STATO"?') is True
        assert strategy.plan("x", {}).route == "sql"

    def test_raggruppa_le_richieste_per(self):
        strategy = ExplicitGroupBySQLStrategy()
        assert strategy.matches('Raggruppa le richieste per "LINEA PRODOTTO".') is True


class TestMultiRouteStrategy:
    """Fast path for questions that need multiple engines"""

    def test_count_and_notes(self):
        strategy = MultiRouteStrategy()
        assert strategy.matches("How many open cases and which notes mention vibration?") is True
        plan = strategy.plan("How many open cases and which notes mention vibration?", {})
        assert plan.route == "multi"
        assert plan.execution == "multi"


class TestQueryRouterIntegration:
    """Integration tests for real-world questions"""

    def test_explicit_python_fast_path(self):
        """Explicit Python requests bypass LLM"""
        router = QueryRouter()
        plan = router.plan("usa python per contare", {})
        assert plan.route == "python"
        assert plan.reason == "user_explicit"

    def test_detail_request_fast_path(self):
        """Detail requests bypass LLM"""
        router = QueryRouter()
        plan = router.plan("stampa i dettagli di quelle righe", {})
        assert plan.route == "sql"
        assert plan.reason == "detail_request"

    def test_status_lookup_fast_path(self):
        """Status+ID lookups bypass LLM"""
        router = QueryRouter()
        plan = router.plan("qual e lo stato della matricola 123", {})
        assert plan.route == "status"
        assert plan.reason == "status_by_id"

    def test_status_request_lookup_fast_path(self):
        router = QueryRouter()
        plan = router.plan('Qual e lo stato della richiesta "UT#001644"?', {})
        assert plan.route == "status"

    def test_simple_count_goes_to_heuristic(self):
        """Simple counts use the deterministic count route first"""
        router = QueryRouter()
        plan = router.plan("quanti abbiamo in totale", {})
        assert plan.route == "count"
        assert plan.reason == "simple_count"
        assert plan.ordered_routes() == ("count", "sql", "python", "semantic")

    @pytest.mark.parametrize(
        "question",
        [
            "How many open cases?",
            "Quanti ticket WIP abbiamo?",
            "Quante richieste hanno stato closed?",
        ],
    )
    def test_status_only_counts_stay_deterministic(self, question):
        assert QueryRouter().plan(question, {}).route == "count"

    @pytest.mark.parametrize(
        "question",
        [
            "Quanti ticket del cliente Acme?",
            "How many Robot cases?",
            "Quanti casi chiusi del prodotto Robot?",
            "How many open cases for customer Acme?",
            "Quanti ticket ci sono dopo il 1 gennaio 2025?",
        ],
    )
    def test_filtered_counts_go_to_sql(self, question):
        assert QueryRouter().plan(question, {}).route == "sql"

    def test_count_with_problem_word_stays_count(self):
        router = QueryRouter()
        plan = router.plan("How many problem cases do we have?", {})
        assert plan.route == "count"

    def test_filtered_count_fast_path(self):
        """Simple count/filter queries use SQLRouteStrategy"""
        router = QueryRouter()
        plan = router.plan("quanti abbiamo con stato = closed", {})
        assert plan.route == "sql"
        assert plan.reason == "sql_capable"

    def test_filter_request_fast_path(self):
        """Filter requests use SQLRouteStrategy"""
        router = QueryRouter()
        plan = router.plan("filtra solo per le macchine WIP", {})
        assert plan.route == "sql"
        assert plan.reason == "sql_capable"

    def test_date_filters_go_to_sql(self):
        router = QueryRouter()
        plan = router.plan('Quante richieste hanno "DATA PRESA CARICO UT" dal 2025-11-12 in poi?', {})
        assert plan.route == "sql"

    def test_latest_request_goes_to_sql(self):
        router = QueryRouter()
        plan = router.plan('Qual e la richiesta piu recente secondo "DATA PRESA CARICO UT"?', {})
        assert plan.route == "sql"

    def test_grouped_count_goes_to_sql(self):
        """Grouped counts use relational aggregation, not Python."""
        router = QueryRouter()
        plan = router.plan("conta quanti WIP abbiamo per priorita", {})
        assert plan.route == "sql"
        assert plan.reason == "grouped_count"

    def test_group_by_llm_routing(self):
        """Group by questions go to MultiColumnCountStrategy"""
        router = QueryRouter()
        plan = router.plan("raggruppa per priorita e stato", {})
        assert plan.route == "sql"

    def test_complex_filter_llm_routing(self):
        """Complex filter with groupby goes to python, simple filter goes to DuckDB"""
        router = QueryRouter()
        # Simple filter → DuckDB
        plan = router.plan("conta quanti WIP con priorita HIGH", {})
        assert plan.route == "sql"
        plan = router.plan("conta quanti WIP con priorità HIGH", {})
        assert plan.route == "sql"
        # With groupby → SQL
        plan = router.plan("conta WIP per priorita", {})
        assert plan.route == "sql"

    def test_structured_semantic_goes_to_hybrid(self):
        router = QueryRouter()
        plan = router.plan("Find open cases similar to motor vibration", {})
        assert plan.route == "hybrid"
        assert plan.ordered_routes() == ("hybrid", "sql", "python")

        plan = router.plan('Tra le richieste con "STATO" = "NEW", quali citano "macchina"?', {})
        assert plan.route == "hybrid"

    def test_count_plus_notes_goes_to_multi(self):
        router = QueryRouter()
        plan = router.plan("How many open cases and which notes mention vibration?", {})
        assert plan.route == "multi"
        assert plan.execution == "multi"
        assert plan.ordered_routes() == ("multi", "sql", "semantic", "python")

    def test_explicit_group_by_goes_to_sql_before_python(self):
        router = QueryRouter()
        plan = router.plan('Raggruppa le richieste per "LINEA PRODOTTO".', {})
        assert plan.route == "sql"

    def test_calculation_over_notes_is_not_treated_as_semantic_search(self):
        router = QueryRouter()
        assert router.plan("Confronta le note tra i due file", {}).route == "python"
        assert router.plan("Calcola la percentuale di note mancanti", {}).route == "python"

    def test_semantic_route_is_disabled_when_index_has_no_columns(self):
        metadata = {"tables": [{"columns": ["note"], "semantic_columns": []}]}
        plan = QueryRouter().plan("Trova note simili a una perdita", metadata)
        assert plan.route == "sql"
        assert plan.ordered_routes() == ("sql", "python")
        assert plan.reason.endswith("semantic_unavailable")

    def test_empty_metadata_keeps_semantic_unit_test_behavior(self):
        assert QueryRouter().plan("Trova note simili a una perdita", {}).route == "semantic"

    def test_any_semantic_column_keeps_semantic_capability(self):
        metadata = {"tables": [{"columns": ["note"], "semantic_columns": ["note"]}]}
        assert QueryRouter().plan("Trova note simili a una perdita", metadata).route == "semantic"


class TestRealWorldQuestions:
    """Test actual questions from the chat log that were problematic before"""

    def test_original_problematic_question(self):
        """'conta quanti WIP abbiamo al momento, e raggruppa per priorita'
        
        This was the original question that failed - it went to count route
        and returned only aggregates, not the cross-filtered data.
        It should use a SQL GROUP BY.
        """
        router = QueryRouter()
        plan = router.plan("conta quanti WIP abbiamo al momento, e raggruppa per priorita", {})
        assert plan.route == "sql", f"Grouped question must go to SQL, got {plan.route}"
        assert "multi" in plan.reason.lower() or "group" in plan.reason.lower() or "filter" in plan.reason.lower()

    def test_use_python_or_db(self):
        """'usa python oppure il DB per contare quante sono in WIP con priorita = HIGH'
        
        User explicitly asks for python - should use fast path.
        """
        router = QueryRouter()
        plan = router.plan("usa python oppure il DB per contare quante sono in WIP con priorita = HIGH", {})
        assert plan.route == "python"
        assert plan.reason == "user_explicit"

    def test_print_details(self):
        """'stampa i dettagli di quelle 2 richieste'
        
        User asks for details - should use fast path.
        """
        router = QueryRouter()
        plan = router.plan("stampa i dettagli di quelle 2 richieste", {})
        assert plan.route == "sql"
        assert plan.reason == "detail_request"

    def test_must_use_python(self):
        """'devi usare python e lavorare sul file principale'
        
        User insists on python - should use fast path.
        """
        router = QueryRouter()
        plan = router.plan("devi usare python e lavorare sul file principale", {})
        assert plan.route == "python"
        assert plan.reason == "user_explicit"

    def test_count_wip_by_priority_variants(self):
        """Various ways to ask for WIP count by priority"""
        router = QueryRouter()
        questions = [
            "conta i WIP per priorita",
            "quanti WIP ci sono per ogni priorita",
            "how many WIP by priority",
            "count WIP grouped by priority",
        ]
        for q in questions:
            plan = router.plan(q, {})
            assert plan.route == "sql", f"Question '{q}' should route to SQL, got {plan.route}"


class TestBackwardCompatibility:
    """Ensure old API still works"""

    def test_classify_function(self):
        from app.routing import classify
        # These should still work, though routing may differ
        assert classify("usa python") == "python"
        assert classify("trova simili") in ["semantic", "python"]  # LLM may decide

    def test_plan_route_function(self):
        from app.routing import plan_route
        result = plan_route("usa python", {})
        assert result["route"] == "python"
        assert result["reason"] == "user_explicit"


def test_llm_router_tolerates_non_numeric_confidence(monkeypatch):
    class Planner:
        def generate(self, system, user, model, temperature=0.2):
            return '{"route": "sql", "reason": "ambiguous filter", "confidence": "high", "candidates": ["sql", "python"]}'

    import app.routing as routing

    monkeypatch.setattr(routing, "load_settings", lambda: {})
    monkeypatch.setattr(routing, "get_llm_provider", lambda _settings=None: (Planner(), "fake"))

    plan = QueryRouter(strategies=[LLMRouterStrategy()]).plan("trova i record richiesti", {})

    assert plan.route == "sql"
    assert plan.confidence == 0.85
    assert plan.ordered_routes() == ("sql", "python", "semantic")
