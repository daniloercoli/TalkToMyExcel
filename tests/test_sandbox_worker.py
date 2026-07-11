import importlib.util
from pathlib import Path

import pytest


WORKER = Path(__file__).resolve().parents[1] / "sandbox" / "worker.py"


def load_worker():
    spec = importlib.util.spec_from_file_location("sandbox_worker", WORKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_suggest_semantic_columns_prefers_descriptive_text():
    pytest.importorskip("pandas")
    worker = load_worker()
    columns = [
        {"name": "Matricola", "semantic_score": 0.2, "avg_length": 8, "numeric_ratio": 0.0, "date_ratio": 0.0},
        {"name": "Problem Description", "semantic_score": 4.2, "avg_length": 60, "numeric_ratio": 0.0, "date_ratio": 0.0},
        {"name": "Status", "semantic_score": 0.1, "avg_length": 6, "numeric_ratio": 0.0, "date_ratio": 0.0},
    ]

    assert worker.suggest_semantic_columns(columns) == ["Problem Description"]


@pytest.mark.parametrize(("suffix", "separator"), [(".csv", ","), (".tsv", "\t")])
def test_profile_file_normalizes_cp1252_delimited_files_to_utf8(tmp_path, suffix, separator):
    pytest.importorskip("pandas")
    worker = load_worker()
    source = tmp_path / f"attività{suffix}"
    output = tmp_path / "output"
    output.mkdir()
    source.write_bytes(f"Città{separator}Priorità\nForlì{separator}Alta\n".encode("cp1252"))

    profile = worker.profile_file(source, output)

    assert profile["sheets"][0]["preview"] == [{"Città": "Forlì", "Priorità": "Alta"}]
    assert (output / "sheet_1.csv").read_text(encoding="utf-8") == "Città,Priorità\nForlì,Alta\n"
