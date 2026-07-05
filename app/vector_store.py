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


def query_rows(path: Path, collection_name: str, embedding: list[float], top_k: int = 12) -> list[dict]:
    collection = chroma_client(path).get_or_create_collection(collection_name)
    result = collection.query(query_embeddings=[embedding], n_results=top_k)
    rows = []
    ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for row_id, doc, meta, distance in zip(ids, docs, metas, distances):
        rows.append(
            {
                "id": row_id,
                "text": doc,
                "metadata": meta or {},
                "distance": distance,
            }
        )
    return rows
