from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.logging_config import log
from app.providers.factory import get_llm_provider, load_settings


@dataclass(frozen=True)
class RouteTool:
    name: str
    title: str
    description: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    examples: tuple[str, ...]
    fallbacks: tuple[str, ...]

    def prompt_block(self) -> str:
        use_when = "; ".join(self.use_when)
        avoid_when = "; ".join(self.avoid_when)
        examples = "; ".join(self.examples)
        return (
            f"- `{self.name}` ({self.title}): {self.description}\n"
            f"  Use when: {use_when}\n"
            f"  Avoid when: {avoid_when}\n"
            f"  After-sales examples: {examples}\n"
            f"  Fallback/subroute order: {', '.join(self.fallbacks)}"
        )


ROUTE_TOOLS: dict[str, RouteTool] = {
    "count": RouteTool(
        name="count",
        title="deterministic count summary",
        description="Cheap DuckDB summaries for broad row counts and status distributions.",
        use_when=(
            "the user asks for total counts or open/closed/WIP distributions",
            "there is no specific product, date, note, or similarity condition",
        ),
        avoid_when=(
            "the question needs grouping by arbitrary columns",
            "the question combines counts with notes, examples, or fuzzy matching",
        ),
        examples=(
            "How many cases are open or closed?",
            "Quanti ticket abbiamo in totale?",
        ),
        fallbacks=("count", "sql", "python", "semantic"),
    ),
    "status": RouteTool(
        name="status",
        title="exact status lookup",
        description="Deterministic lookup for status by serial, asset, machine, or matricola.",
        use_when=(
            "the user asks the status/state of a specific serial or machine",
            "an identifier is present in the question",
        ),
        avoid_when=(
            "the user asks for similar issues or free-text notes",
            "there is no concrete identifier to look up",
        ),
        examples=(
            "What is the status for serial MX-1001?",
            "Qual e lo stato della matricola 123?",
        ),
        fallbacks=("status", "sql", "semantic", "python"),
    ),
    "sql": RouteTool(
        name="sql",
        title="structured DuckDB query",
        description="Read-only text-to-SQL over spreadsheet columns for filters, exact values, dates, and simple aggregations.",
        use_when=(
            "the question can be answered from structured columns",
            "the user asks for product/status/date/customer filters, exact lookups, SUM, AVG, MIN, MAX, or GROUP BY",
        ),
        avoid_when=(
            "the user needs cross-file dataframe logic or custom calculations",
            "the core condition is fuzzy text similarity in notes or problem descriptions",
        ),
        examples=(
            "Quanti WIP con priorita HIGH?",
            "Show closed Robot cases opened after June 1st.",
        ),
        fallbacks=("sql", "python", "semantic"),
    ),
    "semantic": RouteTool(
        name="semantic",
        title="semantic vector search",
        description="Vector retrieval over selected semantic columns such as problem descriptions, checks, notes, and solutions.",
        use_when=(
            "the user asks for similar symptoms, fuzzy wording, notes, causes, checks, or solutions",
            "there is no strong structured filter that must be applied first",
        ),
        avoid_when=(
            "the user asks only for exact counts or exact structured filters",
            "the user combines a structured filter with fuzzy text retrieval",
        ),
        examples=(
            "Find cases similar to motor vibration.",
            "Trova note simili a perdita pressione.",
        ),
        fallbacks=("semantic", "sql", "python"),
    ),
    "hybrid": RouteTool(
        name="hybrid",
        title="structured filter then semantic retrieval",
        description="SQL first narrows eligible rows by structured columns, then vector search ranks text inside that subset.",
        use_when=(
            "the question has both exact filters and fuzzy notes/symptoms",
            "structured fields like status, product, priority, date, customer, or matricola should constrain semantic retrieval",
        ),
        avoid_when=(
            "there is no reliable structured filter",
            "the user only needs a count or a dataframe calculation",
        ),
        examples=(
            "Find open cases similar to motor vibration.",
            "Nei WIP Robot, quali note parlano di perdita idraulica?",
        ),
        fallbacks=("hybrid", "semantic", "sql", "python"),
    ),
    "multi": RouteTool(
        name="multi",
        title="multi-route synthesis",
        description="Runs multiple engines and synthesizes their evidence when the user asks for more than one kind of answer.",
        use_when=(
            "the question asks for a count plus examples, notes, or similar cases",
            "the answer needs structured results and semantic evidence together",
        ),
        avoid_when=(
            "one route can fully answer the question",
            "the user explicitly requests Python-only analysis",
        ),
        examples=(
            "How many open cases and which notes mention vibration?",
            "Conta i WIP e mostrami casi simili a perdita pressione.",
        ),
        fallbacks=("multi", "sql", "semantic", "python"),
    ),
    "python": RouteTool(
        name="python",
        title="sandboxed pandas analysis",
        description="Sandboxed Python for multi-step logic, cross-file comparisons, ratios, correlations, missing IDs, and row dumps.",
        use_when=(
            "the user explicitly asks for Python or CSV work",
            "the question needs dataframe operations, custom calculations, comparisons, or all matching rows",
        ),
        avoid_when=(
            "a cheap deterministic count or SQL query is sufficient",
            "the task is simple semantic similarity over notes",
        ),
        examples=(
            "Confronta due file e trova matricole mancanti.",
            "Calcola il rapporto warranty_cost/amount piu alto.",
        ),
        fallbacks=("python", "sql", "semantic"),
    ),
}
ROUTE_TOOL_ORDER = ("count", "status", "sql", "semantic", "hybrid", "multi", "python")
ROUTES = set(ROUTE_TOOLS)
FALLBACKS = {name: tool.fallbacks for name, tool in ROUTE_TOOLS.items()}


def route_tool_prompt() -> str:
    return "\n".join(ROUTE_TOOLS[name].prompt_block() for name in ROUTE_TOOL_ORDER)


@dataclass
class RoutePlan:
    route: str
    reason: str
    confidence: float = 1.0
    candidates: tuple[str, ...] = field(default_factory=tuple, compare=False)
    source: str = field(default="", compare=False)
    execution: str = field(default="fallback", compare=False)

    def ordered_routes(self) -> tuple[str, ...]:
        return self.candidates or (self.route,)


def complete_plan(plan: RoutePlan, source: str = "") -> RoutePlan:
    candidates = tuple(dict.fromkeys((plan.route, *plan.candidates, *FALLBACKS.get(plan.route, ()))))
    execution = "multi" if plan.route == "multi" else plan.execution
    return RoutePlan(
        route=plan.route,
        reason=plan.reason,
        confidence=plan.confidence,
        candidates=candidates,
        source=plan.source or source,
        execution=execution,
    )


def has_phrase(text: str, phrases: set[str] | list[str]) -> bool:
    low = text.lower()
    for phrase in phrases:
        pattern = r"(?<!\w)" + re.escape(phrase.lower()) + r"(?!\w)"
        if re.search(pattern, low):
            return True
    return False


COUNT_INTENT_WORDS = {"how many", "count", "quanti", "quante", "numero", "conta"}
DETAIL_INTENT_WORDS = {
    "stampa dettagli",
    "stampa i dettagli",
    "mostra righe",
    "mostra le righe",
    "elenca",
    "dettagli di",
    "righe corrispondenti",
    "show details",
    "print details",
    "list all",
    "quali note",
    "which notes",
    "quali casi",
    "which cases",
}
SEMANTIC_INTENT_WORDS = {
    "similar",
    "simili",
    "similarity",
    "assomiglia",
    "somiglia",
    "like this",
    "casi simili",
    "similar issues",
    "note",
    "notes",
    "descrizione",
    "description",
    "sintomo",
    "sintomi",
    "symptom",
    "symptoms",
    "problema",
    "problem",
    "cita",
    "citano",
    "citare",
    "parla",
    "parlano",
    "mention",
    "mentions",
}
FUZZY_SEMANTIC_WORDS = {
    "similar",
    "simili",
    "similarity",
    "assomiglia",
    "somiglia",
    "like this",
    "casi simili",
    "similar issues",
    "note",
    "notes",
    "quali note",
    "which notes",
    "parlano di",
    "cita",
    "citano",
    "citare",
    "parla",
    "parlano",
    "mention",
    "mentions",
}
STRUCTURED_FILTER_WORDS = {
    "open",
    "opened",
    "closed",
    "wip",
    "aperto",
    "aperta",
    "aperti",
    "aperte",
    "chiuso",
    "chiusa",
    "chiusi",
    "chiuse",
    "status",
    "state",
    "stato",
    "priorita",
    "priorità",
    "priority",
    "prodotto",
    "product",
    "linea",
    "customer",
    "cliente",
    "data",
    "date",
    "serial",
    "matricola",
    "asset",
    "machine",
}
MULTI_INTENT_JOINERS = {" e ", " and ", " anche ", " also ", " oltre ", " together ", " insieme ", " poi "}
EXPLICIT_GROUP_SQL_PHRASES = {
    "per ciascun valore",
    "for each value",
    "raggruppa le richieste per",
    "raggruppa i casi per",
    "group requests by",
    "group cases by",
}


class RouteStrategy(ABC):
    @abstractmethod
    def matches(self, question: str) -> bool:
        pass

    @abstractmethod
    def plan(self, question: str, metadata: dict) -> RoutePlan:
        pass


class ExplicitPythonStrategy(RouteStrategy):
    """User explicitly asks for Python/CSV"""

    def matches(self, question: str) -> bool:
        phrases = [
            "usa python",
            "use python",
            "devi usare python",
            "prova con python",
            "prova python",
            "scrivi python",
            "python script",
            "col csv",
            "dal csv",
            "file csv",
        ]
        return has_phrase(question, phrases)

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        return RoutePlan(route="python", reason="user_explicit", candidates=("python", "sql", "semantic"))


class DetailRequestStrategy(RouteStrategy):
    """User asks for details/rows listing"""

    def matches(self, question: str) -> bool:
        phrases = [
            "stampa dettagli",
            "stampa i dettagli",
            "mostra righe",
            "mostra le righe",
            "elenca",
            "dettagli di",
            "righe corrispondenti",
            "show details",
            "print details",
            "list all",
        ]
        return has_phrase(question, phrases) or has_phrase(question, {"stampa", "dettagli", "mostra", "elenca"})

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        return RoutePlan(route="python", reason="detail_request", candidates=("python", "sql", "semantic"))


class StatusIdStrategy(RouteStrategy):
    """Status lookup by serial/machine ID"""

    STATUS_WORDS = {"status", "state", "stato"}
    ID_WORDS = {"matricola", "serial", "serial number", "asset", "machine", "richiesta", "request", "ticket", "case"}

    def matches(self, question: str) -> bool:
        return has_phrase(question, self.STATUS_WORDS) and has_phrase(question, self.ID_WORDS)

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        return RoutePlan(route="status", reason="status_by_id", candidates=("status", "sql", "semantic", "python"))


class MultiRouteStrategy(RouteStrategy):
    """Questions that explicitly need more than one engine"""

    def matches(self, question: str) -> bool:
        low = question.lower()
        if any(phrase in low for phrase in EXPLICIT_GROUP_SQL_PHRASES):
            return False
        has_count = has_phrase(low, COUNT_INTENT_WORDS)
        has_text_need = has_phrase(low, SEMANTIC_INTENT_WORDS) or has_phrase(low, DETAIL_INTENT_WORDS)
        has_joiner = any(joiner in low for joiner in MULTI_INTENT_JOINERS)
        asks_for_examples = "quali" in low or "which" in low or "show" in low or "mostra" in low
        return has_count and has_text_need and (has_joiner or asks_for_examples)

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        return RoutePlan(route="multi", reason="multi_intent", candidates=("sql", "semantic", "python"), execution="multi")


class HybridStructuredSemanticStrategy(RouteStrategy):
    """SQL filters first, vector search second"""

    def matches(self, question: str) -> bool:
        low = question.lower()
        return has_phrase(low, SEMANTIC_INTENT_WORDS) and has_phrase(low, STRUCTURED_FILTER_WORDS)

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        return RoutePlan(route="hybrid", reason="structured_semantic_search", candidates=("hybrid", "semantic", "sql", "python"))


class ExplicitGroupBySQLStrategy(RouteStrategy):
    """Explicit column distributions are better served by SQL GROUP BY"""

    def matches(self, question: str) -> bool:
        low = question.lower()
        return any(phrase in low for phrase in EXPLICIT_GROUP_SQL_PHRASES)

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        return RoutePlan(route="sql", reason="explicit_group_by", candidates=("sql", "python", "semantic"))


class SemanticSearchStrategy(RouteStrategy):
    """Similarity and fuzzy search over semantic columns"""

    def matches(self, question: str) -> bool:
        return has_phrase(question, FUZZY_SEMANTIC_WORDS)

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        return RoutePlan(route="semantic", reason="semantic_search", candidates=("semantic", "sql", "python"))


class SQLRouteStrategy(RouteStrategy):
    """Queries that can be solved with DuckDB SQL (filters, aggregates, counts)"""

    def matches(self, question: str) -> bool:
        indicators = {
            "quanti", "count", "how many", "numero", "conta", "quante",
            "filtra", "filter", "dove", "where",
            "somma", "sum", "totale", "total", "media", "average", "avg",
            "massimo", "minimum", "maximum", "minimo", "max",
            "piu recente", "più recente", "most recent", "latest", "recente",
            "dal", "in poi", "from", "after", "before", "prima", "dopo",
        }
        return has_phrase(question, indicators)

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        return RoutePlan(route="sql", reason="sql_capable", candidates=("sql", "python", "semantic"))


class MultiColumnCountStrategy(RouteStrategy):
    """Count with multiple column filters goes to python"""

    COUNT_WORDS = {"how many", "count", "quanti", "quante", "numero", "conta"}
    GROUP_WORDS = {
        " per ",
        " by ",
        "group by",
        "grouped by",
        "raggruppa",
        "raggruppati",
        "per ogni",
        "for each",
    }

    def matches(self, question: str) -> bool:
        low = question.lower()
        has_count = has_phrase(low, self.COUNT_WORDS)
        has_group = any(phrase in low for phrase in self.GROUP_WORDS)
        return has_group and (has_count or "raggruppa" in low or "group" in low)

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        return RoutePlan(route="python", reason="multi_column_filter", candidates=("python", "sql", "semantic"))


class PythonCalculationStrategy(RouteStrategy):
    """Fast path for obvious calculation requests"""

    CALC_WORDS = {
        "correlazione",
        "percentuale",
        "differenza",
        "differenze",
        "confronta",
        "confronto",
        "correlation",
        "percentage",
        "difference",
        "compare",
        "calcola",
        "calcolo",
        "rapporto",
        "ratio",
        "mancanti",
        "missing",
        "outlier",
        "anomalia",
        "trend",
        "between columns",
        "multi-step",
        "multi step",
        "dataframe",
    }

    def matches(self, question: str) -> bool:
        return has_phrase(question, self.CALC_WORDS)

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        return RoutePlan(route="python", reason="calculation_requested", candidates=("python", "sql", "semantic"))


class SimpleCountStrategy(RouteStrategy):
    """Simple counts without filters"""

    COUNT_WORDS = {"how many", "count", "quanti", "quante", "numero", "conta"}

    def matches(self, question: str) -> bool:
        low = question.lower()
        if not has_phrase(low, self.COUNT_WORDS):
            return False
        if "=" in low or has_phrase(low, {"where", "dove", "con stato", "with status", "priorita", "priorità", "priority"}):
            return False
        if has_phrase(low, {"dal", "in poi", "from", "after", "before", "prima", "dopo", "data", "date"}):
            return False
        return not any(phrase in low for phrase in MultiColumnCountStrategy.GROUP_WORDS)

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        return RoutePlan(route="count", reason="simple_count", candidates=("count", "sql", "python", "semantic"))


class LLMRouterStrategy(RouteStrategy):
    """LLM-based routing for all complex cases"""

    def matches(self, question: str) -> bool:
        return True  # Always matches as the intelligent fallback

    def plan(self, question: str, metadata: dict) -> RoutePlan:
        settings = load_settings()
        try:
            llm, model = get_llm_provider(settings)
            system = (
                "You are a routing expert for TalkToMyExcel, a spreadsheet Q&A system. "
                "Analyze the user question and return JSON with route, reason, confidence, and candidates fields. "
                "\n\n"
                "AVAILABLE ROUTE TOOLS:\n"
                f"{route_tool_prompt()}\n\n"
                "ROUTE RULES:\n"
                "   - User explicitly says 'use python' or 'use the CSV file'\n"
                "     => primary python, candidates ['python','sql','semantic'].\n"
                "   - 'status of serial/machine X' => primary status.\n"
                "   - Similar/fuzzy wording without a hard filter => primary semantic.\n"
                "   - Similar/fuzzy wording with status/product/date/customer/priority filters => primary hybrid.\n"
                "   - A question that asks for counts AND notes/examples/similar cases => primary multi.\n"
                "   - Simple total/status count => primary count.\n"
                "   - Filters, exact values, SUM/AVG/MIN/MAX => primary sql.\n"
                "   - Grouped or multi-column counts => primary python, with sql fallback.\n"
                "   - Comparisons across files, missing IDs, ratios, correlations, custom dataframe work => primary python.\n"
                "   - When uncertain between SQL and Python, choose SQL for relational aggregation and Python for multi-step analysis.\n"
                "   - For route='multi', candidates are the subroutes to run after multi, usually ['sql','semantic'].\n"

                "\n"
                "Examples:\n"
                "- 'count WIP by priority' -> python, candidates ['python','sql','semantic']\n"
                "- 'how many open cases do we have?' -> count, candidates ['count','sql','python','semantic']\n"
                "- 'which IDs are in file A but not file B?' -> python, candidates ['python','sql','semantic']\n"
                "- 'what's the status of serial 123' -> status, candidates ['status','sql','semantic','python']\n"
                "- 'show me similar issues' -> semantic, candidates ['semantic','sql','python']\n"
                "- 'find open cases similar to motor vibration' -> hybrid, candidates ['hybrid','semantic','sql','python']\n"
                "- 'how many open cases and which notes mention vibration?' -> multi, candidates ['sql','semantic']\n"
                "\n"
                "Return ONLY valid JSON like: "
                "{\"route\":\"python\",\"reason\":\"grouped count\",\"confidence\":0.82,"
                "\"candidates\":[\"python\",\"sql\",\"semantic\"]}"
            )
            user = f"User Question:\n{question}\n\n"
            if metadata.get("tables"):
                user += f"Available data:\n{self._metadata_summary(metadata)}\n\n"
            user += "Analyze and return the correct route."

            response = llm.generate(system, user, model=model, temperature=0.0)
            payload = self._parse_json_object(response)
            planned = str(payload.get("route", "")).strip().lower()
            reason = str(payload.get("reason", "llm_decision"))
            confidence = self._confidence(payload.get("confidence", 0.85))
            execution = str(payload.get("execution") or payload.get("mode") or "fallback").strip().lower()
            if planned == "multi":
                execution = "multi"
            raw_candidates = payload.get("candidates") or []
            candidates = tuple(
                route
                for route in (str(item).strip().lower() for item in raw_candidates)
                if route in ROUTES
            )

            if planned in ROUTES:
                log.info(
                    "llm_routing_decision",
                    extra={
                        "question_preview": question[:100],
                        "route": planned,
                        "reason": reason,
                        "llm_response": response[:200],
                    },
                )
                return RoutePlan(
                    route=planned,
                    reason=reason,
                    confidence=confidence,
                    candidates=candidates,
                    execution=execution,
                )

        except Exception as exc:
            log.warning("llm_router_failed", extra={"error": str(exc)[:300]})

        return RoutePlan(route="semantic", reason="llm_fallback", confidence=0.3, candidates=("semantic", "sql", "python"))

    def _metadata_summary(self, metadata: dict) -> str:
        summary = {
            "tables": [
                {
                    "filename": t.get("filename"),
                    "sheet": t.get("sheet"),
                    "rows": t.get("rows"),
                    "columns": t.get("columns", []),
                    "semantic_columns": t.get("semantic_columns", []),
                }
                for t in metadata.get("tables", [])
            ]
        }
        return json.dumps(summary, ensure_ascii=False)

    def _parse_json_object(self, text: str) -> dict:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start >= 0 and end > start:
                return json.loads(stripped[start : end + 1])
            raise

    def _confidence(self, value: object) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.85
        return min(max(confidence, 0.0), 1.0)


class QueryRouter:
    """Main router class that orchestrates strategy selection"""

    def __init__(self, strategies: list[RouteStrategy] | None = None):
        if strategies:
            self.strategies = strategies
        else:
            # Fast heuristics first, LLM as final fallback
            self.strategies = [
                ExplicitPythonStrategy(),      # User explicitly says "use python"
                StatusIdStrategy(),            # Status + serial number lookup
                MultiRouteStrategy(),          # Questions requiring multiple engines
                HybridStructuredSemanticStrategy(),  # Structured filter plus semantic search
                ExplicitGroupBySQLStrategy(),  # Explicit column distributions
                SemanticSearchStrategy(),      # Similarity/fuzzy search
                DetailRequestStrategy(),       # User asks for details/rows
                PythonCalculationStrategy(),   # Complex calculations/comparisons
                MultiColumnCountStrategy(),    # Grouped aggregates
                SimpleCountStrategy(),         # Deterministic count summaries
                SQLRouteStrategy(),            # SQL-capable queries (filter/count/sum)
                LLMRouterStrategy(),           # Intelligent fallback
            ]


    def plan(self, question: str, metadata: dict) -> RoutePlan:
        for strategy in self.strategies:
            if strategy.matches(question):
                plan = complete_plan(strategy.plan(question, metadata), source=type(strategy).__name__)
                log.info(
                    "route_planned",
                    extra={
                        "question_preview": question[:100],
                        "route": plan.route,
                        "reason": plan.reason,
                        "confidence": plan.confidence,
                        "candidates": list(plan.ordered_routes()),
                        "source": plan.source,
                        "execution": plan.execution,
                    },
                )
                return plan
        return RoutePlan(route="semantic", reason="default")


# Backward compatibility
def classify(question: str) -> str:
    router = QueryRouter()
    return router.plan(question, {}).route


def plan_route(question: str, metadata: dict, request_id: str = "") -> dict:
    router = QueryRouter()
    plan = router.plan(question, metadata)
    return {"route": plan.route, "reason": plan.reason}
