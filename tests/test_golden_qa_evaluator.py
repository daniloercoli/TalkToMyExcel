from __future__ import annotations

import json

from scripts.evaluate_golden_qa import evaluate_routes, main, metadata_from_profile, summarize


def synthetic_payload():
    return {
        "profile": {
            "source_file": "synthetic.xlsx",
            "sheets": [
                {
                    "name": "Cases",
                    "rows": 2,
                    "columns": ["status", "problem_description"],
                    "detected": {"semantic": ["problem_description"]},
                }
            ],
        },
        "questions": [
            {
                "id": "Q001",
                "question": "How many cases do we have?",
                "expected_route": "count",
            },
            {
                "id": "Q002",
                "question": "Find open cases similar to motor vibration",
                "expected_route": "hybrid",
            },
            {
                "id": "Q003",
                "question": "How many open cases and which notes mention vibration?",
                "expected_route": "multi",
            },
            {
                "id": "Q004",
                "history": [
                    {"role": "user", "content": "Find cases similar to motor vibration"},
                    {"role": "assistant", "content": "The closest vibration cases are MX-1001 and MX-1003."},
                ],
                "question": "same, but only open",
                "expected_route": "hybrid",
            },
            {
                "id": "Q005",
                "history": [
                    {"role": "user", "content": "Find open cases similar to motor vibration"},
                    {"role": "assistant", "content": "The closest cases are MX-1001 and MX-1003."},
                ],
                "question": "stampa i dettagli di quelle 2 richieste",
                "expected_route": "python",
            },
        ],
    }


def test_metadata_from_profile_keeps_semantic_columns():
    metadata = metadata_from_profile(synthetic_payload()["profile"])

    assert metadata["tables"][0]["sheet"] == "Cases"
    assert metadata["tables"][0]["semantic_columns"] == ["problem_description"]


def test_evaluate_routes_uses_deterministic_router():
    results = evaluate_routes(synthetic_payload())

    assert [result.actual_route for result in results] == ["count", "hybrid", "multi", "hybrid", "python"]
    summary = summarize(results)
    assert summary["accuracy"] == 1.0
    assert summary["contextualized"] == 1
    assert summary["contextualized_matched"] == 1
    assert [result.question_contextualized for result in results] == [False, False, False, True, False]


def test_cli_writes_private_style_report(tmp_path, capsys):
    golden_path = tmp_path / "golden_qa.json"
    output_path = tmp_path / "report.json"
    golden_path.write_text(json.dumps(synthetic_payload()), encoding="utf-8")

    exit_code = main([str(golden_path), "--output", str(output_path)])

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["summary"]["matched"] == 5
    assert '"accuracy": 1.0' in capsys.readouterr().out
