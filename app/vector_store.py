from __future__ import annotations

from pathlib import Path


def chroma_client(path: Path):
    import chromadb

    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))


def reset_collection(path: Path, collection_name: str) -> None:
    client = chroma_client(path)
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    client.get_or_create_collection(collection_name)


def delete_by_workbook_id(path: Path, collection_name: str, workbook_id: str) -> None:
    collection = chroma_client(path).get_or_create_collection(collection_name)
    collection.delete(where={"workbook_id": workbook_id})


def add_rows(path: Path, collection_name: str, rows: list[dict], embeddings: list[list[float]]) -> None:
    if not rows:
        return
    collection = chroma_client(path).get_or_create_collection(collection_name)
    collection.add(
        ids=[row["id"] for row in rows],
        documents=[row["text"] for row in rows],
        metadatas=[row["metadata"] for row in rows],
        embeddings=embeddings,
    )


def query_rows(
    path: Path,
    collection_name: str,
    embedding: list[float],
    top_k: int = 12,
    row_ids: list[str] | set[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    collection = chroma_client(path).get_or_create_collection(collection_name)
    allowed_row_ids = {str(row_id) for row_id in row_ids or [] if row_id}
    where = {"row_id": {"$in": list(allowed_row_ids)}} if allowed_row_ids else None
    n_results = top_k
    try:
        n_results = min(max(top_k * 4, top_k), collection.count())
    except Exception:
        pass
    if n_results <= 0:
        return []
    result = query_collection(collection, embedding, n_results, where)
    rows = []
    ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    seen_row_ids = set()
    for chunk_id, doc, meta, distance in zip(ids, docs, metas, distances):
        meta = meta or {}
        row_id = meta.get("row_id") or chunk_id
        if allowed_row_ids and row_id not in allowed_row_ids:
            continue
        if row_id in seen_row_ids:
            continue
        seen_row_ids.add(row_id)
        rows.append(
            {
                "id": row_id,
                "chunk_id": chunk_id,
                "text": doc,
                "metadata": meta,
                "distance": distance,
            }
        )
    return rows


def query_collection(collection, embedding: list[float], n_results: int, where: dict | None = None) -> dict:
    if not where:
        return collection.query(query_embeddings=[embedding], n_results=n_results)
    try:
        return collection.query(query_embeddings=[embedding], n_results=n_results, where=where)
    except TypeError:
        return collection.query(query_embeddings=[embedding], n_results=n_results)
    except Exception:
        return collection.query(query_embeddings=[embedding], n_results=n_results)
