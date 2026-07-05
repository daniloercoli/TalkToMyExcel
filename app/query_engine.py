from __future__ import annotations

import json
import re

from app.logging_config import log
from app.providers.factory import get_embedding_provider, get_llm_provider, load_settings
from app.python_sandbox import run_python_analysis
from app.stores import Workspace
from app.vector_store import query_rows
from app.workbook import active_workbook, fetch_rows, quote_ident


STATUS_WORDS = {"status", "state", "stato"}
OPEN_WORDS = {"open", "opened", "aperto", "aperta", "aperti", "aperte"}
COUNT_WORDS = {"how many", "count", "quanti", "quante", "numero"}
SIMILAR_WORDS = {"similar", "simili", "similarity", "assomiglia", "like"}
ID_WORDS = {"matricola", "serial", "serial number", "asset", "machine"}
PYTHON_WORDS = {
    "average", "mean", "median", "std", "standard deviation", "correlation", "ratio",
    "percentage", "percent", "trend", "outlier", "anomaly", "diff", "difference",
    "missing", "compare", "between columns", "calcola", "calcolo", "media", "mediana",
    "correlazione", "percentuale", "rapporto", "anomalia", "differenza", "differenze", "mancanti",
    "confronta", "confronto", "colonne",
}
ROUTES = {"count", "status", "semantic", "python"}


def answer_question(workspace: Workspace, question: str, request_id: str = "") -> dict:
    metadata = active_workbook(workspace)
    if not metadata:
        return {"answer": "No workspace data. Upload and import a tabular file first.", "route": "no_dataset", "sources": []}

    route_plan = plan_route(question, metadata, request_id=request_id)
    route = route_plan["route"]
    log.info(
        "query_route",
        extra={"request_id": request_id, "workspace_id": workspace.workspace_id, "route": route},
    )
    if route == "count":
        context = count_context(workspace, metadata, question)
    elif route == "status":
        context = status_context(workspace, metadata, question)
    elif route == "python":
        return python_answer(workspace, metadata, question, route_plan, request_id=request_id)
    else:
        context = semantic_context(workspace, metadata, question)

    if not context["rows"] and route != "count":
        return {
            "answer": "I could not find matching rows in the active workspace data.",
            "route": route,
            "sources": [],
            "debug": context.get("debug", {}),
        }
    return llm_answer(question, context, route)


def classify(question: str) -> str:
    low = question.lower()
    if any(word in low for word in STATUS_WORDS) and any(word in low for word in ID_WORDS):
        return "status"
    if has_any_word(low, PYTHON_WORDS):
        return "python"
    if any(word in low for word in COUNT_WORDS):
        return "count"
    if any(word in low for word in SIMILAR_WORDS):
        return "semantic"
    return "semantic"


def plan_route(question: str, metadata: dict, request_id: str = "") -> dict:
    route = classify(question)
    if route != "semantic":
        return {"route": route, "reason": "heuristic"}

    settings = load_settings()
    try:
        llm, model = get_llm_provider(settings)
        system = (
            "Route spreadsheet questions for TalkToMyExcel. "
            "Return JSON only with route and reason. "
            "Allowed routes: count, status, semantic, python. "
            "Use python for calculations that need arbitrary dataframe logic, numeric column comparisons, "
            "diffs, missing IDs, correlations, medians, anomalies, or multi-step transformations."
        )
        user = f"Question:\n{question}\n\nWorkbook:\n{metadata_summary(metadata)}"
        payload = parse_json_object(llm.generate(system, user, model=model, temperature=0.0))
        planned = str(payload.get("route", "")).strip().lower()
        if planned in ROUTES:
            return {"route": planned, "reason": str(payload.get("reason") or "llm")}
    except Exception as exc:
        log.warning("route_planner_fallback", extra={"request_id": request_id, "error": str(exc)[:300]})
    return {"route": route, "reason": "fallback"}


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
                LIMIT 10
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
    return {"kind": "status", "rows": rows[:20], "sources": source_rows(rows[:20])}


def semantic_context(workspace: Workspace, metadata: dict, question: str) -> dict:
    settings = load_settings()
    embedder, _model = get_embedding_provider(settings)
    query_embedding = embedder.encode_query(question)
    hits = query_rows(workspace.chroma_dir, workspace.chroma_collection, query_embedding, top_k=20)
    rows = fetch_rows(workspace, metadata, [hit["id"] for hit in hits])
    if wants_open(question):
        rows = [row for row in rows if row_has_open_status(row)]
    hit_by_id = {hit["id"]: hit for hit in hits}
    rows.sort(key=lambda row: hit_by_id.get(row.get("row_id"), {}).get("distance", 999))
    return {
        "kind": "semantic",
        "rows": rows[:12],
        "sources": source_rows(rows[:12], hit_by_id),
        "debug": {"semantic_hits": len(hits), "returned_rows": len(rows[:12])},
    }


def python_answer(workspace: Workspace, metadata: dict, question: str, route_plan: dict, request_id: str = "") -> dict:
    try:
        code = generate_python_code(question, metadata)
        result = run_python_analysis(workspace, metadata, code, request_id=request_id)
    except Exception as exc:
        log.warning("python_analysis_failed", extra={"request_id": request_id, "error": str(exc)[:300]})
        return {
            "answer": f"I could not run the Python analysis: {exc}",
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
    }
    if not result.get("ok"):
        return {
            "answer": "The Python analysis failed before producing a reliable answer.",
            "route": "python",
            "sources": [],
            "debug": debug,
        }
    return {"answer": final_python_answer(question, result), "route": "python", "sources": [], "debug": debug}


def generate_python_code(question: str, metadata: dict) -> str:
    settings = load_settings()
    llm, model = get_llm_provider(settings)
    system = (
        "You write Python for a sandboxed spreadsheet analysis. "
        "Return JSON only: {\"code\": \"...\"}. "
        "The code may use pandas and the standard library. "
        "Read /input/manifest.json, then read CSV files from /input using csv filenames in the manifest. "
        "Use the manifest dataset filenames when the question compares multiple uploaded files. "
        "Do not access network resources. Do not read paths outside /input or write outside /output. "
        "Set a variable named answer to a concise JSON-serializable result. "
        "Do not print prose as the final answer; store the final result in answer."
    )
    user = f"Question:\n{question}\n\nWorkbook:\n{metadata_summary(metadata)}"
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
        raise ValueError("Generated Python code is too large")
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
        return llm.generate(system, user, model=model, temperature=settings["chat"].get("temperature", 0.2))
    except Exception as exc:
        log.warning("python_llm_fallback", extra={"error": str(exc)[:300]})
        return fallback_python_answer(result)


def llm_answer(question: str, context: dict, route: str) -> dict:
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
        answer = llm.generate(system, user, model=model, temperature=settings["chat"].get("temperature", 0.2))
    except Exception as exc:
        log.warning("llm_fallback", extra={"error": str(exc)[:300]})
        answer = fallback_answer(context)
    return {"answer": answer, "route": route, "sources": context.get("sources", []), "debug": context.get("debug", {})}


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
    return json.dumps(payload, ensure_ascii=False, default=str)[:10000]


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
    match = re.search(r"```(?:python)?\s*(.*?)```", text, re.S)
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


def has_any_word(text: str, words: set[str]) -> bool:
    return any(re.search(r"\b" + re.escape(word) + r"\b", text) for word in words)


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
