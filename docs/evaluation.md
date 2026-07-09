# Evaluation Workflow

TalkToMyExcel supports a private golden Q/A workflow for tuning routing and answer quality without committing production data.

## Golden Q/A File

Keep production-derived golden sets under `private/`.

Recommended shape:

```json
{
  "source_file": "example.xlsx",
  "profile": {
    "sheets": [
      {
        "name": "Cases",
        "rows": 100,
        "columns": ["status", "problem_description"],
        "detected": {
          "semantic": ["problem_description"]
        }
      }
    ]
  },
  "questions": [
    {
      "id": "Q001",
      "question": "How many cases do we have?",
      "expected_route": "count",
      "expected_answer": {"rows": 100},
      "verification": "Exact row count.",
      "columns": [],
      "row_refs": []
    }
  ]
}
```

Follow-up questions can include recent chat history. The route evaluator uses it only to resolve the current question for routing metrics:

```json
{
  "id": "Q002",
  "history": [
    {"role": "user", "content": "Find cases similar to motor vibration"},
    {"role": "assistant", "content": "The closest vibration cases are MX-1001 and MX-1003."}
  ],
  "question": "same, but only open",
  "expected_route": "hybrid"
}
```

`private/` is gitignored, so generated files such as `private/golden_qa.json`, `private/golden_qa.md`, and `private/golden_qa_eval.json` stay local.

## Route Evaluation

Run deterministic routing evaluation:

```bash
.venv/bin/python scripts/evaluate_golden_qa.py private/golden_qa.json --output private/golden_qa_eval.json
```

This mode avoids the LLM fallback. It is best for checking whether cheap routing heuristics cover the known after-sales question patterns.

Run full router evaluation, including LLM fallback:

```bash
.venv/bin/python scripts/evaluate_golden_qa.py private/golden_qa.json --use-llm-router --output private/golden_qa_eval.json
```

Use a threshold in CI-like checks:

```bash
.venv/bin/python scripts/evaluate_golden_qa.py private/golden_qa.json --min-accuracy 0.9
```

The script prints only summary metrics by default, including how many questions were contextualized from history. Use `--verbose` for per-question route IDs, expected routes, actual routes, reasons, and candidate chains. Avoid sharing verbose output when the golden set contains production-derived questions.

## Interpreting Results

- `count`: broad deterministic summaries, such as total rows or open/closed distributions.
- `sql`: exact filters, dates, simple aggregates, and explicit column distributions such as "per ciascun valore di X".
- `semantic`: fuzzy search over selected semantic columns.
- `hybrid`: structured SQL filter first, semantic ranking second.
- `multi`: multiple subroutes synthesized into one answer.
- `python`: dataframe logic, cross-file comparisons, ratios, missing IDs, and row dumps.

When a mismatch appears, decide whether the route is truly wrong or the expected route should be updated. For example, an explicit "group by this column" question is normally better as `sql`, even if the grouped column is status-like.
