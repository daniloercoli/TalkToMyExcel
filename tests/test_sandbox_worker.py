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
