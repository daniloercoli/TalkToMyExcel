import json
import subprocess
import sys
from pathlib import Path

import pytest


RUNNER = Path(__file__).resolve().parents[1] / "sandbox" / "python_runner.py"


def test_python_runner_serializes_answer(tmp_path):
    code = tmp_path / "analysis.py"
    result = tmp_path / "result.json"
    code.write_text("answer = {'missing': ['A-2'], 'count': 1}", encoding="utf-8")

    completed = subprocess.run([sys.executable, str(RUNNER), str(code), str(result)], check=False)

    assert completed.returncode == 0
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["answer"] == {"missing": ["A-2"], "count": 1}


def test_python_runner_reports_code_errors(tmp_path):
    code = tmp_path / "analysis.py"
    result = tmp_path / "result.json"
    code.write_text("raise RuntimeError('bad calculation')", encoding="utf-8")

    completed = subprocess.run([sys.executable, str(RUNNER), str(code), str(result)], check=False)

    assert completed.returncode == 0
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert "bad calculation" in payload["error"]


def test_python_runner_serializes_nested_pandas_values(tmp_path):
    pytest.importorskip("pandas")
    code = tmp_path / "analysis.py"
    result = tmp_path / "result.json"
    code.write_text(
        "import pandas as pd\n"
        "answer = pd.DataFrame([{'city': 'Forlì', 'when': pd.Timestamp('2026-07-11'), 'value': pd.NA}])\n",
        encoding="utf-8",
    )

    completed = subprocess.run([sys.executable, str(RUNNER), str(code), str(result)], check=False)

    assert completed.returncode == 0
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["answer"] == [{"city": "Forlì", "when": "2026-07-11 00:00:00", "value": None}]
