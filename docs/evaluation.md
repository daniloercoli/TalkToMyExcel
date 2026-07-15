# Routing Evaluation Workflow

`scripts/evaluate_golden_qa.py` evaluates **route selection only**. It does not
import a workbook, execute DuckDB/Chroma/Python routes, call `/api/query`, or
score answer correctness and citations.

## Golden file

Keep production-derived golden sets under the gitignored `private/` directory.
The canonical schema keeps `source_file` at the top level:

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
      "expected_route": "count"
    }
  ]
}
```

Follow-up questions can include recent history:

```json
{
  "id": "Q002",
  "history": [
    {"role": "user", "content": "Find cases similar to motor vibration"},
    {"role": "assistant", "content": "The closest cases are MX-1001 and MX-1003."}
  ],
  "question": "same, but only open",
  "expected_route": "hybrid"
}
```

The evaluator contextualizes the current question exactly as the application
does before routing. It does not score the text stored in the assistant-history
entry.

## Run route evaluation

Run deterministic evaluation:

```bash
.venv/bin/python scripts/evaluate_golden_qa.py private/golden_qa.json --output private/golden_qa_eval.json
```

This uses the production router's strategy instances and order, omitting only
the LLM fallback. It is deterministic and does not require provider access.

Include the production LLM fallback for ambiguous questions:

```bash
.venv/bin/python scripts/evaluate_golden_qa.py private/golden_qa.json --use-llm-router --output private/golden_qa_eval.json
```

This mode uses the same router instance as the application and can make provider
requests. Keep questions and reports private.

Use a route-accuracy threshold in CI-like checks:

```bash
.venv/bin/python scripts/evaluate_golden_qa.py private/golden_qa.json --min-accuracy 0.9
```

The JSON report includes `"evaluation_scope": "routing_only"`. The script
prints summary metrics by default; `--verbose` adds per-question expected and
actual routes, reasons, candidate chains, and contextualization state.

## Interpret route results

- `count`: broad deterministic summaries.
- `sql`: exact filters, dates, simple aggregates, and explicit distributions.
- `semantic`: fuzzy search over selected semantic columns.
- `hybrid`: structured SQL filtering followed by semantic ranking.
- `multi`: several subroutes combined into one response.
- `python`: dataframe logic, cross-file comparisons, ratios, or missing IDs.

A mismatch can mean either a router regression or an outdated expected route.
For example, an explicit group-by question normally belongs to `sql`, even when
the grouped column resembles a status field.

## Answer-quality checks are separate

Fields such as `expected_answer`, `row_refs`, and `verification` may be useful as
manual annotations, but this script deliberately ignores them. Answer-quality
testing needs a fixed synthetic workbook loaded into a test application, actual
`/api/query` execution, and route-appropriate assertions (for example an exact
count, expected row IDs, or required source fields). Do not report routing
accuracy as answer accuracy.
