from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

from app.config import Config


CASES = [
    {
        "matricola": "MX-1001",
        "status": "open",
        "problem_description": "Motor vibration at high speed after belt replacement",
        "intervention_notes": "Checked alignment and replaced worn bearing",
        "opened_at": "2026-06-10",
        "amount": "1000",
        "warranty_cost": "250",
    },
    {
        "matricola": "MX-1002",
        "status": "closed",
        "problem_description": "Hydraulic pressure drop during warm cycle",
        "intervention_notes": "Replaced valve seal and tested pressure",
        "opened_at": "2026-05-18",
        "amount": "800",
        "warranty_cost": "400",
    },
    {
        "matricola": "MX-1003",
        "status": "open",
        "problem_description": "Repeated motor vibration with abnormal noise",
        "intervention_notes": "Inspected motor mount and scheduled balancing",
        "opened_at": "2026-06-22",
        "amount": "1200",
        "warranty_cost": "300",
    },
    {
        "matricola": "MX-1004",
        "status": "closed",
        "problem_description": "Display error on startup after firmware update",
        "intervention_notes": "Reinstalled firmware and reset controller",
        "opened_at": "2026-04-02",
        "amount": "500",
        "warranty_cost": "100",
    },
]

EXPECTED = [{"matricola": f"MX-100{i}"} for i in range(1, 6)]

CSV_CASES = [
    {
        "matricola": "QR-2001",
        "status": "open",
        "problem_description": "Compressor overheating after extended cycle",
        "intervention_notes": "Cleaned fan and verified airflow",
        "opened_at": "2026-07-01",
        "amount": "900",
        "warranty_cost": "450",
    },
    {
        "matricola": "QR-2002",
        "status": "closed",
        "problem_description": "Pump seal leak after pressure spike",
        "intervention_notes": "Replaced pump seal and tested pressure",
        "opened_at": "2026-07-02",
        "amount": "700",
        "warranty_cost": "175",
    },
    {
        "matricola": "QR-2003",
        "status": "open",
        "problem_description": "Sensor drift creates incorrect temperature readings",
        "intervention_notes": "Calibrated sensor and scheduled follow-up",
        "opened_at": "2026-07-03",
        "amount": "600",
        "warranty_cost": "120",
    },
]


class FakeEmbedding:
    def encode_documents(self, texts):
        return [[float(index)] for index, _text in enumerate(texts, start=1)]

    def encode_query(self, text):
        return [1.0]


class FakeLLM:
    def __init__(self):
        self.generated_code = ""

    def generate(self, system, user, model, temperature=0.2):
        if "You write Python" in system:
            self.generated_code = PYTHON_CODE
            return json.dumps({"code": PYTHON_CODE})
        if "Python result" in user:
            question = user.split("\n\nPython result:", 1)[0].lower()
            if "q2_cases" in question or "secondo file" in question:
                return "Nel primo file ma non nel secondo ci sono MX-1001, MX-1002, MX-1003 e MX-1004."
            if "rapporto" in question or "ratio" in question:
                return "Il rapporto warranty_cost/amount più alto è MX-1002 con 0.5."
            return "Mancano MX-1005; differenza media amount-warranty_cost: 612.5; casi aperti: 2."
        if "Route: count" in user:
            if "after_sales_cases.xlsx" in user and "q2_cases.csv" in user:
                return "Status counts: open=4, closed=3."
            if "status=closed; count=1" in user:
                return "Status counts: open=2, closed=1."
            return "Status counts: open=2, closed=2."
        if "Route: status" in user:
            if "QR-2003" in user:
                return "QR-2003 is open."
            return "MX-1001 is open."
        if "Route: semantic" in user:
            return "The closest vibration cases are MX-1001 and MX-1003."
        return json.dumps({"route": "semantic", "reason": "default"})


PYTHON_CODE = r"""
import csv
import json
from pathlib import Path

input_dir = Path("/input")
if not input_dir.exists():
    input_dir = Path(__file__).parent
manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
tables = {}
tables_by_file = {}
for item in manifest["tables"]:
    rows = list(csv.DictReader((input_dir / item["csv"]).open(encoding="utf-8")))
    tables.setdefault(item["sheet"], rows)
    tables_by_file.setdefault(item.get("filename"), {})[item["sheet"]] = rows
cases = tables["Cases"]
expected = tables["Expected"]
second_file_rows = tables_by_file.get("q2_cases.csv", {}).get("Data", [])
deltas = [float(row["amount"]) - float(row["warranty_cost"]) for row in cases]
ratios = [(row["matricola"], float(row["warranty_cost"]) / float(row["amount"])) for row in cases]
highest_ratio = max(ratios, key=lambda item: item[1])
answer = {
    "missing_matricole": sorted({row["matricola"] for row in expected} - {row["matricola"] for row in cases}),
    "average_delta": sum(deltas) / len(deltas),
    "open_cases": sum(1 for row in cases if row["status"].lower() == "open"),
    "highest_warranty_ratio": {"matricola": highest_ratio[0], "ratio": highest_ratio[1]},
    "first_file_not_in_second": sorted({row["matricola"] for row in cases} - {row["matricola"] for row in second_file_rows}),
}
"""


@pytest.fixture
def client_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "change-me-now")
    monkeypatch.setattr(Config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(Config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(Config, "SETTINGS_FILE", tmp_path / "data" / "settings.json")
    monkeypatch.setattr(Config, "USERS_FILE", tmp_path / "data" / "users.json")

    import app.app as app_module
    import app.query_engine as query_engine
    import app.workbook as workbook
    from app.python_sandbox import export_workbook_inputs

    fake_llm = FakeLLM()
    indexed_rows = []
    reset_calls = []

    def fake_reset_collection(*_args, **_kwargs):
        reset_calls.append(True)
        indexed_rows.clear()

    monkeypatch.setattr(app_module, "profile_in_sandbox", fake_profile_in_sandbox)
    monkeypatch.setattr(workbook, "get_embedding_provider", lambda _settings: (FakeEmbedding(), "fake-embedding"))
    monkeypatch.setattr(workbook, "reset_collection", fake_reset_collection)
    monkeypatch.setattr(workbook, "add_rows", lambda _path, _collection, rows, _embeddings: indexed_rows.extend(rows))
    monkeypatch.setattr(
        workbook,
        "delete_by_workbook_id",
        lambda _path, _collection, workbook_id: indexed_rows.__setitem__(
            slice(None), [row for row in indexed_rows if row["metadata"]["workbook_id"] != workbook_id]
        ),
    )
    monkeypatch.setattr(query_engine, "get_llm_provider", lambda _settings=None: (fake_llm, "fake-chat"))
    monkeypatch.setattr(query_engine, "get_embedding_provider", lambda _settings=None: (FakeEmbedding(), "fake-embedding"))
    monkeypatch.setattr(query_engine, "query_rows", lambda *_args, **_kwargs: fake_query_rows(indexed_rows))
    monkeypatch.setattr(
        query_engine,
        "run_python_analysis",
        lambda workspace, metadata, code, request_id="": local_python_analysis(
            tmp_path, export_workbook_inputs, workspace, metadata, code
        ),
    )

    app = app_module.create_app()
    app.config["TESTING"] = True
    return {
        "client": app.test_client(),
        "indexed_rows": indexed_rows,
        "reset_calls": reset_calls,
        "fake_llm": fake_llm,
    }


def test_excel_upload_import_and_query_routes(client_env):
    client = client_env["client"]
    login = client.post("/login", data={"email": "admin@example.com", "password": "change-me-now"})
    assert login.status_code == 302
    with client.session_transaction() as session:
        admin_id = session["user_id"]

    no_dataset = ask(client, "How many open cases do we have?")
    assert no_dataset["route"] == "no_dataset"

    too_short = client.post("/api/query", json={"question": "hi"})
    assert too_short.status_code == 400

    bad_upload = client.post(
        "/api/staging",
        data={"file": (io.BytesIO(b"not a workbook"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert bad_upload.status_code == 400
    assert "Unsupported file type" in bad_upload.get_json()["error"]

    staging = client.post(
        "/api/staging",
        data={"file": (io.BytesIO(make_xlsx()), "after_sales_cases.xlsx")},
        content_type="multipart/form-data",
    )
    assert staging.status_code == 200
    staging_payload = staging.get_json()
    assert [sheet["name"] for sheet in staging_payload["profile"]["sheets"]] == ["Cases", "Expected"]

    imported = client.post(
        "/api/workbooks",
        json={
            "staging_id": staging_payload["staging_id"],
            "sheets": ["Cases", "Expected"],
            "semantic_columns": {"Cases": ["problem_description", "intervention_notes"]},
            "replace_existing": False,
        },
    )
    assert imported.status_code == 200
    active = client.get("/api/workbooks/active").get_json()["active"]
    assert active["datasets"][0]["filename"] == "after_sales_cases.xlsx"
    assert {table["sheet"] for table in active["tables"]} == {"Cases", "Expected"}
    assert all(table["table"].startswith("wb_") for table in active["tables"])
    assert len(client_env["indexed_rows"]) == 4

    count = ask(client, "How many cases are open or closed?")
    assert count["route"] == "count"
    assert "open=2" in count["answer"]

    status = ask(client, "What is the status for serial MX-1001?")
    assert status["route"] == "status"
    assert "open" in status["answer"]

    semantic = ask(client, "Find cases similar to motor vibration")
    assert semantic["route"] == "semantic"
    assert "MX-1001" in semantic["answer"]
    assert [source["row"] for source in semantic["sources"]] == [1, 3]
    assert {source["file"] for source in semantic["sources"]} == {"after_sales_cases.xlsx"}

    python = ask(
        client,
        "Confronta le colonne amount e warranty_cost, calcola la differenza media e trova matricole mancanti",
    )
    assert python["route"] == "python"
    assert python["debug"]["execution_status"] == "ok"
    assert "MX-1005" in python["answer"]
    assert "612.5" in python["answer"]
    assert "csv.DictReader" in client_env["fake_llm"].generated_code

    ratio = ask(client, "Calcola il rapporto warranty_cost su amount e dimmi la matricola piu alta")
    assert ratio["route"] == "python"
    assert "MX-1002" in ratio["answer"]
    assert "0.5" in ratio["answer"]

    replacement_staging = client.post(
        "/api/staging",
        data={"file": (io.BytesIO(make_csv(CSV_CASES)), "q2_cases.csv")},
        content_type="multipart/form-data",
    )
    assert replacement_staging.status_code == 200
    replacement_payload = replacement_staging.get_json()
    assert replacement_payload["profile"]["sheets"][0]["name"] == "Data"

    appended = client.post(
        "/api/workbooks",
        json={
            "staging_id": replacement_payload["staging_id"],
            "sheets": ["Data"],
            "semantic_columns": {"Data": ["problem_description", "intervention_notes"]},
            "replace_existing": False,
        },
    )
    assert appended.status_code == 200
    appended_active = appended.get_json()["active"]
    assert [dataset["filename"] for dataset in appended_active["datasets"]] == [
        "after_sales_cases.xlsx",
        "q2_cases.csv",
    ]
    assert {table["sheet"] for table in appended_active["tables"]} == {"Cases", "Expected", "Data"}
    assert len(client_env["indexed_rows"]) == 7
    assert client_env["reset_calls"] == []

    combined_count = ask(client, "How many cases are open or closed?")
    assert combined_count["route"] == "count"
    assert "open=4" in combined_count["answer"]
    assert "closed=3" in combined_count["answer"]

    csv_status = ask(client, "What is the status for serial QR-2003?")
    assert csv_status["route"] == "status"
    assert "open" in csv_status["answer"]
    assert csv_status["sources"][0]["file"] == "q2_cases.csv"

    comparison = ask(
        client,
        "Confronta after_sales_cases.xlsx e q2_cases.csv: quali matricole sono nel primo file ma non nel secondo file?",
    )
    assert comparison["route"] == "python"
    assert comparison["debug"]["execution_status"] == "ok"
    assert "MX-1001" in comparison["answer"]
    assert "MX-1004" in comparison["answer"]

    workspace_upload_dir = Config.UPLOAD_DIR / "workspaces" / admin_id
    workbook_dir = Config.DATA_DIR / "workspaces" / admin_id / "workbook"
    xlsx_staging_dir = workspace_upload_dir / "staging" / staging_payload["staging_id"]
    csv_staging_dir = workspace_upload_dir / "staging" / replacement_payload["staging_id"]
    assert (xlsx_staging_dir / "after_sales_cases.xlsx").exists()
    assert (csv_staging_dir / "q2_cases.csv").exists()
    assert (csv_staging_dir / "prepared" / "sheet_1.csv").exists()

    csv_dataset_id = next(dataset["id"] for dataset in appended_active["datasets"] if dataset["filename"] == "q2_cases.csv")
    deleted = client.delete(f"/api/workbooks/{csv_dataset_id}")
    assert deleted.status_code == 200
    after_delete = deleted.get_json()["active"]
    assert [dataset["filename"] for dataset in after_delete["datasets"]] == ["after_sales_cases.xlsx"]
    assert {table["sheet"] for table in after_delete["tables"]} == {"Cases", "Expected"}
    assert len(client_env["indexed_rows"]) == 4
    assert all(row["metadata"]["filename"] == "after_sales_cases.xlsx" for row in client_env["indexed_rows"])
    assert xlsx_staging_dir.exists()
    assert not csv_staging_dir.exists()

    import duckdb

    conn = duckdb.connect(str(workbook_dir / "data.duckdb"), read_only=True)
    tables_after_csv_delete = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    conn.close()
    assert tables_after_csv_delete == {table["table"] for table in after_delete["tables"]}

    xlsx_dataset_id = after_delete["datasets"][0]["id"]
    deleted_last = client.delete(f"/api/workbooks/{xlsx_dataset_id}")
    assert deleted_last.status_code == 200
    assert deleted_last.get_json()["active"] is None
    assert client_env["indexed_rows"] == []
    assert len(client_env["reset_calls"]) == 1
    assert not xlsx_staging_dir.exists()
    assert not workbook_dir.exists()


def ask(client, question):
    response = client.post("/api/query", json={"question": question})
    assert response.status_code == 200
    return response.get_json()


def fake_profile_in_sandbox(input_path: Path, output_dir: Path, request_id: str = ""):
    output_dir.mkdir(parents=True, exist_ok=True)
    if input_path.suffix == ".xlsx":
        assert input_path.read_bytes().startswith(b"PK")
        write_csv(output_dir / "sheet_1.csv", CASES)
        write_csv(output_dir / "sheet_2.csv", EXPECTED)
        return {
            "filename": input_path.name,
            "sheets": [
                sheet_profile("Cases", "sheet_1.csv", CASES, ["problem_description", "intervention_notes"]),
                sheet_profile("Expected", "sheet_2.csv", EXPECTED, []),
            ],
            "default_sheets": ["Cases"],
        }
    if input_path.suffix == ".csv":
        rows = list(csv.DictReader(input_path.open(encoding="utf-8")))
        write_csv(output_dir / "sheet_1.csv", rows)
        return {
            "filename": input_path.name,
            "sheets": [sheet_profile("Data", "sheet_1.csv", rows, ["problem_description", "intervention_notes"])],
            "default_sheets": ["Data"],
        }
    raise AssertionError(f"unexpected test input: {input_path}")


def sheet_profile(name, csv_name, rows, suggested):
    columns = [
        {
            "name": column,
            "non_empty_ratio": 1.0,
            "unique_ratio": 1.0,
            "avg_length": 20.0,
            "max_length": 80,
            "numeric_ratio": 0.0,
            "date_ratio": 0.0,
            "semantic_score": 2.0 if column in suggested else 0.0,
        }
        for column in rows[0]
    ]
    return {
        "name": name,
        "csv": csv_name,
        "rows": len(rows),
        "columns": columns,
        "preview": rows[:2],
        "suggested_semantic_columns": suggested,
    }


def fake_query_rows(indexed_rows):
    return [
        {"id": indexed_rows[0]["id"], "distance": 0.05},
        {"id": indexed_rows[2]["id"], "distance": 0.08},
    ]


def local_python_analysis(tmp_path, export_workbook_inputs, workspace, metadata, code):
    with tempfile.TemporaryDirectory(prefix="python-run-", dir=tmp_path) as run_dir_name:
        run_dir = Path(run_dir_name)
        input_dir = run_dir / "input"
        output_dir = run_dir / "output"
        input_dir.mkdir()
        output_dir.mkdir()
        manifest = export_workbook_inputs(workspace, metadata, input_dir)
        code_path = input_dir / "analysis.py"
        result_path = output_dir / "result.json"
        code_path.write_text(code, encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "sandbox" / "python_runner.py"),
                str(code_path),
                str(result_path),
            ],
            check=False,
        )
        assert completed.returncode == 0
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["elapsed_ms"] = 1
        result["tables"] = manifest["tables"]
        result["datasets"] = manifest["datasets"]
        return result


def write_csv(path, rows):
    columns = list(rows[0])
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(csv_cell(row[column]) for column in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def csv_cell(value):
    text = str(value)
    if any(char in text for char in [",", '"', "\n"]):
        return '"' + text.replace('"', '""') + '"'
    return text


def make_csv(rows):
    columns = list(rows[0])
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(csv_cell(row[column]) for column in columns))
    return ("\n".join(lines) + "\n").encode()


def make_xlsx():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", ROOT_RELS)
        zf.writestr("xl/workbook.xml", WORKBOOK)
        zf.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        zf.writestr("xl/worksheets/sheet1.xml", worksheet_xml("Cases", CASES))
        zf.writestr("xl/worksheets/sheet2.xml", worksheet_xml("Expected", EXPECTED))
    buffer.seek(0)
    return buffer.read()


def worksheet_xml(_name, rows):
    columns = list(rows[0])
    all_rows = [dict(zip(columns, columns)), *rows]
    body = []
    for row_index, row in enumerate(all_rows, start=1):
        cells = []
        for col_index, column in enumerate(columns, start=1):
            ref = f"{column_letter(col_index)}{row_index}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{xml_escape(row[column])}</t></is></c>')
        body.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(body)}</sheetData></worksheet>'


def column_letter(index):
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def xml_escape(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

WORKBOOK = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Cases" sheetId="1" r:id="rId1"/>
    <sheet name="Expected" sheetId="2" r:id="rId2"/>
  </sheets>
</workbook>"""

WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>"""
