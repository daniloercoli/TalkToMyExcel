import duckdb

from app.python_sandbox import export_workbook_inputs
from app.stores import Workspace


def test_export_workbook_inputs_writes_manifest_and_csv(tmp_path):
    workspace = Workspace("user", "user", tmp_path / "data", tmp_path / "uploads")
    workspace.workbook_dir.mkdir(parents=True)
    conn = duckdb.connect(str(workspace.duckdb_path))
    conn.execute("CREATE TABLE sheet_data AS SELECT 'A-1' AS matricola, '10' AS amount")
    conn.close()
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    metadata = {
        "datasets": [
            {
                "id": "demo-workbook",
                "filename": "demo.csv",
                "tables": [
                    {
                        "workbook_id": "demo-workbook",
                        "filename": "demo.csv",
                        "sheet": "Data",
                        "table": "sheet_data",
                        "rows": 1,
                        "columns": ["matricola", "amount"],
                    }
                ],
            }
        ],
        "tables": [
            {
                "workbook_id": "demo-workbook",
                "filename": "demo.csv",
                "sheet": "Data",
                "table": "sheet_data",
                "rows": 1,
                "columns": ["matricola", "amount"],
            }
        ],
    }

    manifest = export_workbook_inputs(workspace, metadata, input_dir)

    assert manifest["datasets"] == [{"id": "demo-workbook", "filename": "demo.csv"}]
    assert manifest["tables"][0]["filename"] == "demo.csv"
    assert manifest["tables"][0]["csv"] == "sheet_data.csv"
    assert (input_dir / "manifest.json").exists()
    assert "A-1" in (input_dir / "sheet_data.csv").read_text(encoding="utf-8")
