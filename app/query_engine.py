from __future__ import annotations

import json
import re

from app.logging_config import log
from app.providers.factory import get_embedding_provider, get_llm_provider, load_settings
from app.python_sandbox import run_python_analysis
from app.routing import QueryRouter, RoutePlan
from app.stores import Workspace
from app.vector_store import query_rows
from app.workbook import active_workbook, fetch_rows, quote_ident

OPEN_WORDS = {"open", "opened", "aperto", "aperta", "aperti", "aperte"}
STATUS_WORDS = {"status", "state", "stato"}
ID_WORDS = {"matricola", "serial", "serial number", "asset", "machine", "richiesta", "request", "ticket", "case"}
FOLLOWUP_WORDS = {
    "same",
    "same thing",
    "stessa cosa",
    "stesso",
    "stessa",
    "stessi",
    "stesse",
    "idem",
    "again",
    "ancora",
    "those",
    "these",
    "that",
    "them",
    "quelli",
    "quelle",
    "questi",
    "queste",
    "quello",
    "quella",
    "gli altri",
    "le altre",
    "altri",
    "altre",
    "others",
    "but only",
    "ma solo",
}
DETAIL_ROUTE_WORDS = {"stampa", "dettagli", "mostra", "elenca", "show details", "print details", "list all"}
MAX_LLM_MESSAGES = 20
MAX_SQL_ROWS = 200
MAX_HYBRID_FILTER_ROWS = 1000
MAX_INLINE_CONTEXT_MESSAGES = 2
MAX_INLINE_CONTEXT_CHARS = 2_000
CHARS_PER_TOKEN = 4
ROUTER = QueryRouter()


def classify(question: str) -> str:
    return ROUTER.plan(question, {}).route


def plan_route(question: str, metadata: dict, request_id: str = "") -> dict:
    plan = ROUTER.plan(question, metadata)
    return {"route": plan.route, "reason": plan.reason}


def question_prompt(question: str, metadata: dict, conversation_history: list[dict] | None = None) -> str:
    context = recent_conversation_context(conversation_history)
    parts = []
    if context:
        parts.append(
            "Recent context (use only to resolve follow-up references in the current question):\n"
            f"{context}"
        )
    parts.append(f"Question:\n{question}")
    parts.append(f"Workbook Schema:\n{metadata_summary(metadata)}")
    return "\n\n".join(parts)


def recent_conversation_context(conversation_history: list[dict] | None = None) -> str:
    if not conversation_history:
        return ""
    lines = []
    content_limit = MAX_INLINE_CONTEXT_CHARS // MAX_INLINE_CONTEXT_MESSAGES
    for message in conversation_history[-MAX_INLINE_CONTEXT_MESSAGES:]:
        role = str(message.get("role") or "user").strip() or "user"
        content = str(message.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content[:content_limit]}")
    return "\n".join(lines)


def has_any_phrase(text: str, phrases: set[str]) -> bool:
    low = text.lower()
    return any(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", low) for phrase in phrases)


def contextual_followup_question(question: str, conversation_history: list[dict] | None = None) -> str:
    if not has_any_phrase(question, FOLLOWUP_WORDS):
        return question
    context = recent_conversation_context(conversation_history)
    if not context:
        return question
    return f"Recent context:\n{context}\n\nFollow-up question:\n{question}"


def routing_question(question: str, conversation_history: list[dict] | None = None) -> str:
    if has_any_phrase(question, DETAIL_ROUTE_WORDS):
        return question
    return contextual_followup_question(question, conversation_history)


def generate_sql_query(question: str, metadata: dict, conversation_history: list[dict] | None = None) -> str:
    """Generates a DuckDB SQL query based on the user's question and table metadata"""
    settings = load_settings()
    llm, model = get_llm_provider(settings)
    
    system = (
        "You are a DuckDB SQL expert. Generate a SQL query that answers the user's question based on the provided schema. "
        "Return ONLY a valid JSON object: {\"sql\": \"SELECT ...\"}. "
        "RULES:\n"
        "1. Use only the table names provided in the metadata.\n"
        "2. Use double quotes for identifiers (table/column names) to handle spaces/special characters: e.g., \"My Table\".\n"
        "3. For filters, use the = operator and single quotes for values: e.g., \"Status\" = 'WIP'.\n"
        "4. Use standard DuckDB SQL syntax.\n"
        "5. If the question asks for a count, use count(*).\n"
        "6. For detail requests (e.g. 'details of ID 123'), use SELECT * FROM \"table\" WHERE \"ID_COLUMN\" = '123'.\n"
        "7. For grouped counts, use GROUP BY and return the group column plus count(*).\n"
        "8. Only generate read-only SELECT queries. No INSERT, UPDATE, DELETE, CREATE, DROP, COPY, PRAGMA, ATTACH, INSTALL, or LOAD.\n"
        "9. Only return the SQL query, no explanations."
    )
    
    user = question_prompt(question, metadata, conversation_history)
    
    raw = llm.generate(system, user, model=model, temperature=0.0)
    try:
        payload = parse_json_object(raw)
        return str(payload.get("sql") or "").strip()
    except Exception:
        code = extract_fenced_code(raw)
        return code if code else ""


def generate_hybrid_filter_sql(question: str, metadata: dict, conversation_history: list[dict] | None = None) -> str:
    """Generate a row_id filter query for hybrid SQL + semantic retrieval."""
    settings = load_settings()
    llm, model = get_llm_provider(settings)

    system = (
        "You are a DuckDB SQL expert for hybrid spreadsheet retrieval. "
        "Return ONLY a valid JSON object: {\"sql\": \"SELECT row_id FROM ... WHERE ...\"}. "
        "Your job is to extract ONLY the structured filters from the user question. "
        "The semantic/text part will be handled by vector search later. "
        "RULES:\n"
        "1. If there is no reliable structured filter, return {\"sql\": \"\"}.\n"
        "2. Select only row_id from one provided table, or use UNION ALL when multiple tables apply.\n"
        "3. Use only table and column names from metadata.\n"
        "4. Use double quotes for identifiers and single quotes for values.\n"
        "5. Good structured filters include status/state, product, priority, date, customer, serial, sheet, and filename.\n"
        "6. Do not filter on semantic similarity, notes, symptoms, problem descriptions, or causes.\n"
        "7. Only generate read-only SELECT/WITH SQL. No INSERT, UPDATE, DELETE, CREATE, DROP, COPY, PRAGMA, ATTACH, INSTALL, or LOAD.\n"
        f"8. Add LIMIT {MAX_HYBRID_FILTER_ROWS + 1} unless the query already has a stricter limit.\n"
        "9. Return no explanations."
    )
    user = question_prompt(question, metadata, conversation_history)

    raw = llm.generate(system, user, model=model, temperature=0.0)
    try:
        payload = parse_json_object(raw)
        return str(payload.get("sql") or "").strip()
    except Exception:
        code = extract_fenced_code(raw)
        return code if code else ""


def answer_question(workspace: Workspace, question: str, request_id: str = "", conversation_history: list[dict] | None = None) -> dict:
    metadata = active_workbook(workspace)
    if not metadata:
        return {"answer": "No workspace data. Upload and import a tabular file first.", "route": "no_dataset", "sources": []}

    route_input = routing_question(question, conversation_history)
    route_plan = ROUTER.plan(route_input, metadata)
    log.info(
        "query_route",
        extra={
            "request_id": request_id,
            "workspace_id": workspace.workspace_id,
            "route_contextualized": route_input != question,
            "route_question": route_input[:500],
            "route": route_plan.route,
            "reason": route_plan.reason,
            "candidates": list(route_plan.ordered_routes()),
            "execution": route_plan.execution,
        },
    )
    
    return try_route(workspace, metadata, question, route_plan, request_id, conversation_history)


def try_route(workspace, metadata, question, route_plan: RoutePlan, request_id, conversation_history):
    attempts = []
    last_result = None
    for route in route_plan.ordered_routes():
        result = run_route(workspace, metadata, question, route, route_plan, request_id, conversation_history)
        status = result.pop("_routing_status", "ok")
        detail = result.pop("_routing_detail", "")
        attempts.append({"route": route, "status": status, "detail": detail})
        if status == "ok":
            result["route"] = route
            debug = result.setdefault("debug", {})
            debug["route_plan"] = {
                "primary": route_plan.route,
                "reason": route_plan.reason,
                "confidence": route_plan.confidence,
                "source": route_plan.source,
                "candidates": list(route_plan.ordered_routes()),
                "execution": route_plan.execution,
            }
            debug["route_attempts"] = attempts
            return result
        last_result = result
        log.info(
            "route_attempt_not_usable",
            extra={"request_id": request_id, "route": route, "status": status, "detail": detail},
        )

    debug = (last_result or {}).get("debug", {})
    debug["route_plan"] = {
        "primary": route_plan.route,
        "reason": route_plan.reason,
        "confidence": route_plan.confidence,
        "source": route_plan.source,
        "candidates": list(route_plan.ordered_routes()),
        "execution": route_plan.execution,
    }
    debug["route_attempts"] = attempts
    return {
        "answer": "I could not produce a reliable answer from the active workspace data.",
        "route": route_plan.route,
        "sources": [],
        "debug": debug,
    }


def run_route(workspace, metadata, question, route, route_plan, request_id, conversation_history):
    try:
        if route == "count":
            context = count_context(workspace, metadata, question)
            return answer_from_context(question, context, "count", conversation_history)
        if route == "sql":
            return sql_answer(workspace, metadata, question, request_id, conversation_history)
        if route == "status":
            context = status_context(workspace, metadata, question)
            return answer_from_context(question, context, "status", conversation_history)
        if route == "python":
            result = python_answer(
                workspace,
                metadata,
                question,
                {"route": "python", "reason": route_plan.reason},
                request_id=request_id,
            )
            if result.get("debug", {}).get("execution_status") != "ok":
                result["_routing_status"] = "failed"
                result["_routing_detail"] = result.get("debug", {}).get("stderr_preview") or "python_failed"
            return result
        if route == "semantic":
            context = semantic_context(workspace, metadata, question, conversation_history=conversation_history)
            return answer_from_context(question, context, "semantic", conversation_history)
        if route == "hybrid":
            context = hybrid_semantic_context(workspace, metadata, question, request_id, conversation_history)
            return answer_from_context(question, context, "hybrid", conversation_history)
        if route == "multi":
            return multi_answer(workspace, metadata, question, route_plan, request_id, conversation_history)
        return route_failed(route, f"unknown_route:{route}")
    except Exception as exc:
        log.warning(
            "route_attempt_failed",
            extra={"request_id": request_id, "route": route, "error": str(exc)[:500]},
        )
        return route_failed(route, str(exc)[:500])


def answer_from_context(question: str, context: dict, route: str, conversation_history: list[dict] | None = None) -> dict:
    if not context.get("rows"):
        return {
            "answer": "",
            "route": route,
            "sources": context.get("sources", []),
            "debug": context.get("debug", {}),
            "_routing_status": "no_results",
            "_routing_detail": "no_rows",
        }
    return llm_answer(question, context, route, conversation_history)


def multi_answer(workspace, metadata, question, route_plan: RoutePlan, request_id, conversation_history):
    subroutes = [route for route in route_plan.ordered_routes() if route != "multi"]
    if not subroutes:
        subroutes = ["sql", "semantic"]

    attempts = []
    results = []
    for subroute in subroutes:
        if subroute == "python" and results:
            continue
        result = run_route(workspace, metadata, question, subroute, route_plan, request_id, conversation_history)
        status = result.pop("_routing_status", "ok")
        detail = result.pop("_routing_detail", "")
        attempts.append({"route": subroute, "status": status, "detail": detail})
        if status != "ok":
            continue
        results.append(
            {
                "route": subroute,
                "answer": result.get("answer", ""),
                "sources": result.get("sources", []),
                "debug": result.get("debug", {}),
            }
        )
        if {"sql", "semantic"}.issubset({item["route"] for item in results}):
            break

    if not results:
        return route_failed("multi", "no_subroute_results")

    context = {
        "kind": "multi",
        "rows": [
            {
                "route": item["route"],
                "answer": item["answer"],
                "source_count": len(item["sources"]),
            }
            for item in results
        ],
        "sources": dedupe_sources(source for item in results for source in item["sources"]),
        "debug": {
            "multi_routes": [item["route"] for item in results],
            "multi_attempts": attempts,
            "subroute_debug": {item["route"]: item["debug"] for item in results},
        },
    }
    return llm_answer(question, context, "multi", conversation_history)


def route_failed(route: str, detail: str) -> dict:
    return {
        "answer": "",
        "route": route,
        "sources": [],
        "debug": {"error": detail},
        "_routing_status": "failed",
        "_routing_detail": detail,
    }


def sql_answer(workspace: Workspace, metadata: dict, question: str, request_id: str, conversation_history: list[dict] | None) -> dict:
    import duckdb

    sql = generate_sql_query(question, metadata, conversation_history)
    if not sql:
        return route_failed("sql", "sql_generation_empty")
    try:
        sql = validate_select_sql(sql)
    except ValueError as exc:
        return route_failed("sql", str(exc))

    conn = duckdb.connect(str(workspace.duckdb_path), read_only=True)
    try:
        log.info("sql_execution", extra={"request_id": request_id, "sql": sql[:2000]})
        cursor = conn.execute(sql)
        rows_raw = cursor.fetchmany(MAX_SQL_ROWS + 1)
        columns = [col[0] for col in cursor.description or []]
    except Exception as exc:
        log.warning("sql_route_failed", extra={"request_id": request_id, "error": str(exc)[:500], "sql": sql[:1000]})
        return route_failed("sql", str(exc)[:500])
    finally:
        conn.close()

    truncated = len(rows_raw) > MAX_SQL_ROWS
    rows_raw = rows_raw[:MAX_SQL_ROWS]
    if not rows_raw:
        return {
            "answer": "",
            "route": "sql",
            "sources": [],
            "debug": {"sql": sql, "rows": 0},
            "_routing_status": "no_results",
            "_routing_detail": "sql_returned_no_rows",
        }

    if len(rows_raw) == 1 and len(rows_raw[0]) == 1:
        rows = [{"result": rows_raw[0][0]}]
    else:
        rows = [dict(zip(columns, row)) for row in rows_raw]

    context = {
        "kind": "sql",
        "rows": rows,
        "sources": [],
        "debug": {"sql": sql, "rows": len(rows), "truncated": truncated},
    }
    return llm_answer(question, context, "sql", conversation_history)


def validate_select_sql(sql: str) -> str:
    cleaned = extract_fenced_code(sql) or sql
    cleaned = cleaned.strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("sql_generation_empty")
    if ";" in cleaned:
        raise ValueError("sql_multiple_statements_not_allowed")
    if not re.match(r"^(select|with)\b", cleaned, re.I):
        raise ValueError("sql_must_be_select")
    blocked = r"\b(insert|update|delete|drop|alter|create|attach|copy|pragma|call|install|load|export|import)\b"
    if re.search(blocked, cleaned, re.I):
        raise ValueError("sql_contains_blocked_keyword")
    return cleaned


def count_context(workspace: Workspace, metadata: dict, question: str) -> dict:
    import duckdb

    conn = duckdb.connect(str(workspace.duckdb_path), read_only=True)
    rows = []
    for table in metadata["tables"]:
        status_col = find_column(table["columns"], STATUS_WORDS)
        if status_col:
            result = conn.execute(
                f"""
                SELECT {quote_ident(status_col)} AS status, count(*) AS count
                FROM {quote_ident(table['table'])}
                GROUP BY {quote_ident(status_col)}
                ORDER BY count DESC
                LIMIT 25
                """
            ).fetchall()
            rows.extend(
                {
                    "file": table.get("filename"),
                    "sheet": table["sheet"],
                    "status": status or "",
                    "count": count,
                }
                for status, count in result
            )
        else:
            count = conn.execute(f"SELECT count(*) FROM {quote_ident(table['table'])}").fetchone()[0]
            rows.append({"file": table.get("filename"), "sheet": table["sheet"], "count": count})
    conn.close()
    return {"kind": "count", "rows": rows, "sources": [], "debug": {"tables": len(metadata["tables"])}}


def status_context(workspace: Workspace, metadata: dict, question: str) -> dict:
    import duckdb

    conn = duckdb.connect(str(workspace.duckdb_path), read_only=True)
    tokens = query_tokens(question)
    rows = []
    for table in metadata["tables"]:
        id_col = find_column(table["columns"], ID_WORDS)
        status_col = find_column(table["columns"], STATUS_WORDS)
        if not id_col or not status_col:
            continue
        for token in tokens:
            result = conn.execute(
                f"""
                SELECT row_id, sheet_name, original_row_number, {quote_ident(id_col)}, {quote_ident(status_col)}
                FROM {quote_ident(table['table'])}
                WHERE lower({quote_ident(id_col)}) = lower(?)
                   OR lower({quote_ident(id_col)}) LIKE lower(?)
                """,
                [token, f"%{token}%"],
            ).fetchall()
            rows.extend(
                {
                    "row_id": row_id,
                    "filename": table.get("filename"),
                    "workbook_id": table.get("workbook_id"),
                    "sheet": sheet,
                    "original_row_number": original_row_number,
                    id_col: serial,
                    status_col: status,
                }
                for row_id, sheet, original_row_number, serial, status in result
            )
    conn.close()
    return {"kind": "status", "rows": rows, "sources": source_rows(rows)}


def hybrid_semantic_context(
    workspace: Workspace,
    metadata: dict,
    question: str,
    request_id: str = "",
    conversation_history: list[dict] | None = None,
) -> dict:
    filter_sql = generate_hybrid_filter_sql(question, metadata, conversation_history)
    if not filter_sql:
        return {"kind": "hybrid", "rows": [], "sources": [], "debug": {"hybrid_filter": "empty"}}
    try:
        filter_sql = validate_select_sql(filter_sql)
    except ValueError as exc:
        return {
            "kind": "hybrid",
            "rows": [],
            "sources": [],
            "debug": {"hybrid_filter_sql": filter_sql, "error": str(exc)},
        }

    row_ids, truncated = execute_row_id_sql(workspace, filter_sql, request_id)
    if not row_ids:
        return {
            "kind": "hybrid",
            "rows": [],
            "sources": [],
            "debug": {"hybrid_filter_sql": filter_sql, "filtered_rows": 0, "truncated": truncated},
        }

    context = semantic_context(workspace, metadata, question, candidate_row_ids=row_ids, conversation_history=conversation_history)
    context["kind"] = "hybrid"
    debug = context.setdefault("debug", {})
    debug.update(
        {
            "hybrid_filter_sql": filter_sql,
            "filtered_rows": len(row_ids),
            "filter_truncated": truncated,
        }
    )
    return context


def execute_row_id_sql(workspace: Workspace, sql: str, request_id: str = "") -> tuple[list[str], bool]:
    import duckdb

    conn = duckdb.connect(str(workspace.duckdb_path), read_only=True)
    try:
        log.info("hybrid_filter_sql_execution", extra={"request_id": request_id, "sql": sql[:2000]})
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(MAX_HYBRID_FILTER_ROWS + 1)
        columns = [col[0] for col in cursor.description or []]
    finally:
        conn.close()

    if not columns:
        return [], False
    row_id_index = next((index for index, column in enumerate(columns) if column.lower() == "row_id"), 0)
    truncated = len(rows) > MAX_HYBRID_FILTER_ROWS
    row_ids = []
    seen = set()
    for row in rows[:MAX_HYBRID_FILTER_ROWS]:
        row_id = str(row[row_id_index] or "").strip()
        if row_id and row_id not in seen:
            seen.add(row_id)
            row_ids.append(row_id)
    return row_ids, truncated



def semantic_context(
    workspace: Workspace,
    metadata: dict,
    question: str,
    candidate_row_ids: list[str] | set[str] | tuple[str, ...] | None = None,
    conversation_history: list[dict] | None = None,
) -> dict:
    settings = load_settings()
    embedder, _model = get_embedding_provider(settings)
    retrieval_question = contextual_followup_question(question, conversation_history)
    query_embedding = embedder.encode_query(retrieval_question)
    hits = query_rows(workspace.chroma_dir, workspace.chroma_collection, query_embedding, top_k=20, row_ids=candidate_row_ids)
    rows = fetch_rows(workspace, metadata, [hit["id"] for hit in hits])
    if wants_open(question):
        rows = [row for row in rows if row_has_open_status(row)]
    hit_by_id = {hit["id"]: hit for hit in hits}
    rows.sort(key=lambda row: hit_by_id.get(row.get("row_id"), {}).get("distance", 999))
    return {
        "kind": "semantic",
        "rows": rows[:12],
        "sources": source_rows(rows[:12], hit_by_id),
        "debug": {
            "semantic_hits": len(hits),
            "returned_rows": len(rows[:12]),
            "candidate_rows": len(candidate_row_ids or []),
            "retrieval_contextualized": retrieval_question != question,
        },
    }


def python_answer(workspace: Workspace, metadata: dict, question: str, route_plan: dict, request_id: str = "") -> dict:
    max_attempts = 2
    last_error = None
    
    for attempt in range(max_attempts):
        try:
            code = generate_python_code(question, metadata, last_error)
            log.info(
                "python_code_generated",
                extra={
                    "request_id": request_id,
                    "workspace_id": workspace.workspace_id,
                    "question": question[:500],
                    "code_length": len(code),
                    "code_preview": code[:1000],
                    "attempt": attempt + 1,
                },
            )
            result = run_python_analysis(workspace, metadata, code, request_id=request_id)
            
            if result.get("ok"):
                break
            
            error_text = str(result.get("error") or result.get("stderr") or "")
            if attempt < max_attempts - 1:
                last_error = error_text[:1500]
                log.warning(
                    "python_execution_retry",
                    extra={
                        "request_id": request_id,
                        "error": last_error[:500],
                        "attempt": attempt + 1,
                    },
                )
                continue
                
        except Exception as exc:
            last_error = str(exc)[:1500]
            log.warning(
                "python_code_generation_failed",
                extra={
                    "request_id": request_id,
                    "error": last_error[:500],
                    "attempt": attempt + 1,
                },
            )
            if attempt >= max_attempts - 1:
                return {
                    "answer": f"I could not generate Python code: {exc}",
                    "route": "python",
                    "sources": [],
                    "debug": {"route_reason": route_plan.get("reason"), "execution_status": "failed"},
                }
    
    debug = {
        "route_reason": route_plan.get("reason"),
        "execution_status": "ok" if result.get("ok") else "failed",
        "stdout_preview": str(result.get("stdout") or "")[:1000],
        "stderr_preview": str(result.get("stderr") or result.get("error") or "")[:1000],
        "elapsed_ms": result.get("elapsed_ms"),
        "attempts": attempt + 1,
    }
    if not result.get("ok"):
        log.error(
            "python_execution_failed",
            extra={
                "request_id": request_id,
                "workspace_id": workspace.workspace_id,
                "question": question[:500],
                "error": str(result.get("error") or result.get("stderr") or "")[:1000],
                "stdout": str(result.get("stdout") or "")[:1000],
                "stderr": str(result.get("stderr") or "")[:1000],
            },
        )
        return {
            "answer": "The Python analysis failed before producing a reliable answer.",
            "route": "python",
            "sources": [],
            "debug": debug,
        }
    return {"answer": final_python_answer(question, result), "route": "python", "sources": [], "debug": debug}


def generate_python_code(question: str, metadata: dict, previous_error: str | None = None) -> str:
    settings = load_settings()
    llm, model = get_llm_provider(settings)
    system = (
        "You write Python for a sandboxed spreadsheet analysis. "
        "Return JSON only: {\"code\": \"...\"}. "
        "The code may use pandas and the standard library. "
        "CRITICAL: Read /input/manifest.json first to get the list of CSV files. "
        "Then read data using pd.read_csv('/input/<filename>', encoding='utf-8') where filename comes from manifest['tables'][i]['csv']. "
        "If utf-8 fails, try encoding='latin-1'. "
        "CRITICAL: Do NOT use on_error, on_bad_lines, or any other unsupported parameters. "
        "NEVER use pd.read_excel or read any .xlsx files - only CSV files exist in /input. "
        "CRITICAL: When referencing column names in Python strings, always escape apostrophes: use \"column['name']\" or double quotes \"column'name\". "
        "CRITICAL: NEVER limit the number of rows in your results. Do NOT use .head(), .tail(), or LIMIT. "
        "Process ALL rows from the CSV file. If counting, return the total count. If filtering, return all matching rows. "
        "Examples:\n"
        "  df[df['PRIORITA\\''] == 'CRITICAL']  # apostrophe in column name - returns ALL matching rows\n"
        "  df[df['LINEA PRODOTTO'] == 'Robot']  # space in column name - returns ALL matching rows\n"
        "  df[(df['PRIORITA\\''] == 'CRITICAL') & (df['LINEA PRODOTTO'] == 'Robot')].shape[0]  # combined filter - count ALL\n"
        "  df.groupby('PRIORITA\\'').size().to_dict()  # group by - ALL groups\n"
        "Example pattern:\n"
        "  import json, pandas as pd\n"
        "  with open('/input/manifest.json') as f: manifest = json.load(f)\n"
        "  csv_file = f\"/input/{manifest['tables'][0]['csv']}\"\n"
        "  try: df = pd.read_csv(csv_file, encoding='utf-8')\n"
        "  except: df = pd.read_csv(csv_file, encoding='latin-1')\n"
        "  filtered = df[(df['PRIORITA\\''] == 'CRITICAL') & (df['LINEA PRODOTTO'] == 'Robot')]\n"
        "  answer = filtered.shape[0]  # count ALL matching rows, not just first 6\n"
        "Use the manifest dataset filenames when the question compares multiple uploaded files. "
        "Do not access network resources. Do not read paths outside /input or write outside /output. "
        "Set a variable named answer to a concise JSON-serializable result. "
        "Do not print prose as the final answer; store the final result in answer."
    )
    user = f"Question:\n{question}\n\nWorkbook:\n{metadata_summary(metadata)}"
    if previous_error:
        user += f"\n\nPREVIOUS ERROR - Fix this syntax/runtime error:\n{previous_error}"
    raw = llm.generate(system, user, model=model, temperature=0.0)
    try:
        payload = parse_json_object(raw)
        code = str(payload.get("code") or "").strip()
    except ValueError:
        code = ""
    if not code:
        code = extract_fenced_code(raw)
    if not code:
        raise ValueError("The model did not return Python code")
    if len(code) > 20000:
        raise ValueError("Generated code is too large")
    return code


def final_python_answer(question: str, result: dict) -> str:
    settings = load_settings()
    try:
        llm, model = get_llm_provider(settings)
        system = (
            "You are TalkToMyExcel, an after-sales data analyst. "
            "Answer the user's question from the Python result only. "
            "Be concise and mention calculation limits if the result says so."
        )
        user = f"Question:\n{question}\n\nPython result:\n{compact_python_result(result)}"
        return llm.generate(system, user, model=model, temperature=settings.get("chat", {}).get("temperature", 0.2))
    except Exception as exc:
        log.warning("python_llm_fallback", extra={"error": str(exc)[:300]})
        return fallback_python_answer(result)


def llm_answer(question: str, context: dict, route: str, conversation_history: list[dict] | None = None) -> dict:
    settings = load_settings()
    try:
        llm, model = get_llm_provider(settings)
        system = (
            "You are TalkToMyExcel, an after-sales data analyst. "
            "Answer only from the supplied rows and aggregates. "
            "Mention row references only for real source rows, not for aggregate counts. "
            "If evidence is weak, say so."
        )
        user = f"Question:\n{question}\n\nRoute: {route}\n\nContext:\n{compact_context(context)}"
        messages = [{"role": "system", "content": system}]
        if conversation_history:
            messages.extend(conversation_history[-MAX_LLM_MESSAGES:])
        messages.append({"role": "user", "content": user})
        payload_usage = estimate_llm_payload(messages)
        answer = generate_with_optional_messages(
            llm,
            system,
            user,
            model=model,
            temperature=settings.get("chat", {}).get("temperature", 0.2),
            messages=messages,
        )
    except Exception as exc:
        log.warning("llm_fallback", extra={"error": str(exc)[:300]})
        answer = fallback_answer(context)
        payload_usage = None
    debug = context.get("debug", {})
    if payload_usage:
        debug["llm_payload"] = payload_usage
    return {"answer": answer, "route": route, "sources": context.get("sources", []), "debug": debug}


def estimate_llm_payload(messages: list[dict]) -> dict:
    chars = sum(
        len(str(message.get("role", ""))) + len(str(message.get("content", ""))) for message in messages
    )
    return {
        "chars": chars,
        "estimated_tokens": round(chars / CHARS_PER_TOKEN),
        "messages": len(messages),
        "source": "last_llm_payload",
    }


def generate_with_optional_messages(llm, system: str, user: str, *, model: str, temperature: float, messages: list[dict]) -> str:
    try:
        return llm.generate(system, user, model=model, temperature=temperature, messages=messages)
    except TypeError as exc:
        if "messages" not in str(exc):
            raise
        return llm.generate(system, user, model=model, temperature=temperature)


def compact_context(context: dict) -> str:
    lines = []
    for index, row in enumerate(context["rows"][:20], start=1):
        items = [f"{key}={value}" for key, value in row.items() if value not in (None, "")]
        lines.append(f"[{index}] " + "; ".join(items)[:1500])
    return "\n".join(lines) or "No rows."


def compact_python_result(result: dict) -> str:
    payload = {
        "answer": result.get("answer"),
        "stdout": result.get("stdout"),
        "stderr": result.get("stderr"),
        "tables": result.get("tables"),
    }
    return json.dumps(payload, ensure_ascii=False, default=str, indent=2)[:100000]


def metadata_summary(metadata: dict) -> str:
    summary = {
        "datasets": [
            {
                "id": dataset.get("id"),
                "filename": dataset.get("filename"),
                "tables": [
                    {
                        "sheet": table.get("sheet"),
                        "table": table.get("table"),
                        "rows": table.get("rows"),
                        "columns": table.get("columns", []),
                    }
                    for table in dataset.get("tables", [])
                ],
            }
            for dataset in metadata.get("datasets", [])
        ],
        "tables": [
            {
                "filename": table.get("filename"),
                "sheet": table.get("sheet"),
                "table": table.get("table"),
                "rows": table.get("rows"),
                "columns": table.get("columns", []),
            }
            for table in metadata.get("tables", [])
        ],
    }
    return json.dumps(summary, ensure_ascii=False)


def parse_json_object(text: str) -> dict:
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


def extract_fenced_code(text: str) -> str:
    match = re.search(r"```(?:[A-Za-z0-9_-]+)?\s*(.*?)```", text, re.S)
    return match.group(1).strip() if match else ""


def fallback_answer(context: dict) -> str:
    if context["kind"] == "count":
        return "\n".join(f"{row.get('sheet')}: {row}" for row in context["rows"])
    return compact_context(context)


def fallback_python_answer(result: dict) -> str:
    answer = result.get("answer")
    if isinstance(answer, str):
        return answer or str(result.get("stdout") or "")
    return json.dumps(answer, ensure_ascii=False, indent=2, default=str)


def find_column(columns: list[str], hints: set[str]) -> str | None:
    for column in columns:
        low = column.lower().replace("_", " ")
        if any(hint in low for hint in hints):
            return column
    return None


def query_tokens(question: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", question)
    stop = STATUS_WORDS | ID_WORDS | OPEN_WORDS | {"what", "which", "qual", "quale", "the", "for", "con"}
    return [word for word in words if word.lower() not in stop]


def wants_open(question: str) -> bool:
    low = question.lower()
    return any(word in low for word in OPEN_WORDS)


def row_has_open_status(row: dict) -> bool:
    for key, value in row.items():
        if "status" in key.lower() or "stato" in key.lower() or "state" in key.lower():
            return str(value or "").strip().lower() in OPEN_WORDS
    return False


def source_rows(rows: list[dict], hits: dict | None = None) -> list[dict]:
    sources = []
    for row in rows:
        hit = (hits or {}).get(row.get("row_id"), {})
        sources.append(
            {
                "row_id": row.get("row_id"),
                "file": row.get("workbook_filename") or row.get("filename") or row.get("file"),
                "sheet": row.get("sheet_name") or row.get("sheet"),
                "row": row.get("original_row_number"),
                "distance": hit.get("distance"),
            }
        )
    return sources


def dedupe_sources(sources) -> list[dict]:
    deduped = []
    seen = set()
    for source in sources:
        key = (
            source.get("row_id"),
            source.get("file"),
            source.get("sheet"),
            source.get("row"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(source)
    return deduped
