from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from app.logging_config import log
from app.providers.factory import get_embedding_provider, load_settings
from app.stores import Workspace
from app.vector_store import add_rows, reset_collection


def active_workbook(workspace: Workspace) -> dict | None:
    if not workspace.metadata_path.exists():
        return None
    metadata = normalize_metadata(json.loads(workspace.metadata_path.read_text(encoding="utf-8")))
    return metadata if metadata["datasets"] else None


def staging_manifest(workspace: Workspace, staging_id: str) -> dict:
    path = workspace.staging_dir / staging_id / "manifest.json"
    if not path.exists():
        raise FileNotFoundError("Staging file not found")
    return json.loads(path.read_text(encoding="utf-8"))


def write_staging_manifest(workspace: Workspace, staging_id: str, manifest: dict) -> None:
    path = workspace.staging_dir / staging_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def replace_workbook(
    workspace: Workspace,
    staging_id: str,
    sheet_names: list[str],
    semantic_columns: dict[str, list[str]],
    *,
    replace_existing: bool,
    request_id: str = "",
) -> dict:
    manifest = staging_manifest(workspace, staging_id)
    workbook_id = uuid.uuid4().hex
    if replace_existing and workspace.workbook_dir.exists():
        shutil.rmtree(workspace.workbook_dir)
    workspace.workbook_dir.mkdir(parents=True, exist_ok=True)
    metadata = empty_metadata() if replace_existing else active_workbook(workspace) or empty_metadata()

    import duckdb

    conn = duckdb.connect(str(workspace.duckdb_path))
    tables = []
    selected_sheets = [sheet for sheet in manifest["profile"]["sheets"] if sheet["name"] in sheet_names]
    if not selected_sheets:
        conn.close()
        raise ValueError("Select at least one sheet/table")
    for sheet in selected_sheets:
        table = dataset_table_name(workbook_id, sheet["name"])
        csv_path = Path(manifest["prepared_dir"]) / sheet["csv"]
        columns = [column["name"] for column in sheet["columns"]]
        selected_semantic = [col for col in semantic_columns.get(sheet["name"], []) if col in columns]
        conn.execute(
            f"""
            CREATE TABLE {quote_ident(table)} AS
            SELECT
                {quote_literal(workbook_id)} AS workbook_id,
                {quote_literal(manifest["profile"]["filename"])} AS workbook_filename,
                {quote_literal(sheet["name"])} AS sheet_name,
                row_number() OVER () AS original_row_number,
                {quote_literal(table + "_")} || row_number() OVER () AS row_id,
                *
            FROM read_csv_auto(?, header=true, all_varchar=true)
            """,
            [str(csv_path)],
        )
        tables.append(
            {
                "workbook_id": workbook_id,
                "filename": manifest["profile"]["filename"],
                "sheet": sheet["name"],
                "table": table,
                "rows": sheet["rows"],
                "columns": columns,
                "semantic_columns": selected_semantic,
            }
        )

    dataset = {
        "id": workbook_id,
        "filename": manifest["profile"]["filename"],
        "tables": tables,
        "staging_id": staging_id,
    }
    conn.close()
    metadata["datasets"].append(dataset)
    metadata = normalize_metadata(metadata)
    workspace.metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    rebuild_semantic_index(workspace, metadata, request_id=request_id)
    log.info(
        "workbook_imported",
        extra={"request_id": request_id, "workspace_id": workspace.workspace_id, "workbook_id": workbook_id},
    )
    return metadata


def remove_workbook_dataset(workspace: Workspace, workbook_id: str, request_id: str = "") -> dict | None:
    metadata = active_workbook(workspace)
    if not metadata:
        raise FileNotFoundError("Dataset not found")

    dataset = next((item for item in metadata["datasets"] if item["id"] == workbook_id), None)
    if not dataset:
        raise FileNotFoundError("Dataset not found")

    import duckdb

    if workspace.duckdb_path.exists():
        conn = duckdb.connect(str(workspace.duckdb_path))
        for table in dataset["tables"]:
            conn.execute(f"DROP TABLE IF EXISTS {quote_ident(table['table'])}")
        conn.close()

    metadata["datasets"] = [item for item in metadata["datasets"] if item["id"] != workbook_id]
    metadata = normalize_metadata(metadata)
    if metadata["datasets"]:
        workspace.metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        rebuild_semantic_index(workspace, metadata, request_id=request_id)
    else:
        if workspace.metadata_path.exists():
            workspace.metadata_path.unlink()
        reset_collection(workspace.chroma_dir, workspace.chroma_collection)

    log.info(
        "workbook_removed",
        extra={"request_id": request_id, "workspace_id": workspace.workspace_id, "workbook_id": workbook_id},
    )
    return metadata if metadata["datasets"] else None


def rebuild_semantic_index(workspace: Workspace, metadata: dict, request_id: str = "") -> None:
    import duckdb

    provider, model = get_embedding_provider(load_settings())
    reset_collection(workspace.chroma_dir, workspace.chroma_collection)
    if not metadata.get("tables"):
        return
    conn = duckdb.connect(str(workspace.duckdb_path), read_only=True)
    rows_to_add: list[dict] = []
    for table in metadata["tables"]:
        semantic_columns = table.get("semantic_columns") or []
        if not semantic_columns:
            continue
        select_cols = ", ".join(quote_ident(col) for col in semantic_columns)
        query = f"SELECT row_id, sheet_name, original_row_number, {select_cols} FROM {quote_ident(table['table'])}"
        for row in conn.execute(query).fetchall():
            row_id, sheet_name, original_row_number, *values = row
            parts = []
            for col, value in zip(semantic_columns, values):
                text = str(value or "").strip()
                if text:
                    parts.append(f"{col}: {text}")
            semantic_text = "\n".join(parts).strip()
            if not semantic_text:
                continue
            rows_to_add.append(
                {
                    "id": str(row_id),
                    "text": semantic_text,
                    "metadata": {
                        "row_id": str(row_id),
                        "workbook_id": table.get("workbook_id"),
                        "filename": table.get("filename"),
                        "sheet": str(sheet_name),
                        "table": table["table"],
                        "original_row_number": int(original_row_number),
                    },
                }
            )
    conn.close()

    batch_size = 64
    for start in range(0, len(rows_to_add), batch_size):
        batch = rows_to_add[start : start + batch_size]
        embeddings = provider.encode_documents([row["text"] for row in batch])
        add_rows(workspace.chroma_dir, workspace.chroma_collection, batch, embeddings)
    log.info(
        "semantic_index_rebuilt",
        extra={
            "request_id": request_id,
            "workspace_id": workspace.workspace_id,
            "rows": len(rows_to_add),
            "embedding_model": model,
        },
    )


def fetch_rows(workspace: Workspace, metadata: dict, row_ids: list[str]) -> list[dict]:
    if not row_ids:
        return []
    import duckdb

    by_table: dict[str, list[str]] = {}
    table_by_name = {table["table"]: table for table in metadata["tables"]}
    for row_id in row_ids:
        table = row_id.rsplit("_", 1)[0]
        if table in table_by_name:
            by_table.setdefault(table, []).append(row_id)

    conn = duckdb.connect(str(workspace.duckdb_path), read_only=True)
    rows = []
    for table, ids in by_table.items():
        placeholders = ", ".join(["?"] * len(ids))
        result = conn.execute(
            f"SELECT * FROM {quote_ident(table)} WHERE row_id IN ({placeholders})",
            ids,
        )
        columns = [col[0] for col in result.description]
        for values in result.fetchall():
            row = dict(zip(columns, values))
            row.setdefault("workbook_id", table_by_name[table].get("workbook_id"))
            row.setdefault("workbook_filename", table_by_name[table].get("filename"))
            rows.append(row)
    conn.close()
    return rows


def empty_metadata() -> dict:
    return {"datasets": [], "tables": []}


def normalize_metadata(metadata: dict) -> dict:
    if "datasets" not in metadata:
        dataset = {
            "id": metadata.get("id") or "legacy",
            "filename": metadata.get("filename") or "dataset",
            "staging_id": metadata.get("staging_id"),
            "tables": metadata.get("tables", []),
        }
        metadata = {"datasets": [dataset]}

    datasets = []
    for dataset in metadata.get("datasets", []):
        dataset = dict(dataset)
        dataset.setdefault("id", uuid.uuid4().hex)
        dataset.setdefault("filename", "dataset")
        tables = []
        for table in dataset.get("tables", []):
            table = dict(table)
            table.setdefault("workbook_id", dataset["id"])
            table.setdefault("filename", dataset["filename"])
            tables.append(table)
        dataset["tables"] = tables
        datasets.append(dataset)

    return {"datasets": datasets, "tables": [table for dataset in datasets for table in dataset["tables"]]}


def dataset_table_name(workbook_id: str, sheet_name: str) -> str:
    return f"wb_{workbook_id[:12]}_{table_suffix(sheet_name)}"


def table_name(sheet_name: str) -> str:
    return f"sheet_{table_suffix(sheet_name)}"


def table_suffix(sheet_name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", sheet_name.strip().lower()).strip("_") or "data"


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"
