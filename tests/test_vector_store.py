from app.vector_store import add_rows, chroma_client, delete_by_workbook_id


def test_delete_by_workbook_id_keeps_other_documents(tmp_path):
    rows = [
        {"id": "row-a", "text": "alpha", "metadata": {"workbook_id": "workbook-a"}},
        {"id": "row-b", "text": "beta", "metadata": {"workbook_id": "workbook-b"}},
    ]
    add_rows(tmp_path, "semantic_index", rows, [[1.0], [2.0]])

    delete_by_workbook_id(tmp_path, "semantic_index", "workbook-a")

    collection = chroma_client(tmp_path).get_collection("semantic_index")
    assert collection.get(include=["metadatas"])["ids"] == ["row-b"]
