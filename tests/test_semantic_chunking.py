from app import vector_store
from app.config import env_int
from app.workbook import chunk_semantic_text, semantic_chunk_config


def test_env_int_falls_back_for_invalid_values(monkeypatch):
    monkeypatch.setenv("SEMANTIC_CHUNK_SIZE", "not-a-number")

    assert env_int("SEMANTIC_CHUNK_SIZE", 0) == 0


def test_chunk_semantic_text_keeps_default_single_document():
    assert chunk_semantic_text("short text", chunk_size=0, overlap=0) == ["short text"]


def test_chunk_semantic_text_uses_overlap():
    assert chunk_semantic_text("abcdefghij", chunk_size=4, overlap=1) == ["abcd", "defg", "ghij"]


def test_semantic_chunk_config_clamps_overlap(monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "SEMANTIC_CHUNK_SIZE", 5)
    monkeypatch.setattr(Config, "SEMANTIC_CHUNK_OVERLAP", 99)

    assert semantic_chunk_config() == (5, 4)


def test_query_rows_maps_chunk_ids_back_to_source_rows(monkeypatch, tmp_path):
    class Collection:
        def query(self, query_embeddings, n_results):
            return {
                "ids": [["row_1::chunk_0000", "row_1::chunk_0001", "row_2"]],
                "documents": [["first chunk", "second chunk", "other row"]],
                "metadatas": [[{"row_id": "row_1"}, {"row_id": "row_1"}, {"row_id": "row_2"}]],
                "distances": [[0.1, 0.2, 0.3]],
            }

    class Client:
        def get_or_create_collection(self, collection_name):
            return Collection()

    monkeypatch.setattr(vector_store, "chroma_client", lambda _path: Client())

    hits = vector_store.query_rows(tmp_path, "collection", [0.0], top_k=3)

    assert [hit["id"] for hit in hits] == ["row_1", "row_2"]
    assert hits[0]["chunk_id"] == "row_1::chunk_0000"


def test_query_rows_can_filter_to_candidate_row_ids(monkeypatch, tmp_path):
    seen = {}

    class Collection:
        def count(self):
            return 3

        def query(self, query_embeddings, n_results, where=None):
            seen["where"] = where
            return {
                "ids": [["row_1", "row_2"]],
                "documents": [["first row", "second row"]],
                "metadatas": [[{"row_id": "row_1"}, {"row_id": "row_2"}]],
                "distances": [[0.1, 0.2]],
            }

    class Client:
        def get_or_create_collection(self, collection_name):
            return Collection()

    monkeypatch.setattr(vector_store, "chroma_client", lambda _path: Client())

    hits = vector_store.query_rows(tmp_path, "collection", [0.0], top_k=3, row_ids={"row_2"})

    assert seen["where"] == {"row_id": {"$in": ["row_2"]}}
    assert [hit["id"] for hit in hits] == ["row_2"]
