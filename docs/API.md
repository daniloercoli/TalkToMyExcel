# API

All endpoints require a logged-in session.

## `POST /api/staging`

Multipart upload field: `file`.

Returns a staging profile with sheets, columns, preview rows, and suggested semantic columns.

## `GET /api/workbooks/active`

Returns the active workspace metadata for the current user, including all imported datasets and their DuckDB tables.

## `POST /api/workbooks`

```json
{
  "staging_id": "...",
  "sheets": ["Cases"],
  "semantic_columns": {
    "Cases": ["Problem Description", "Intervention Notes"]
  },
  "replace_existing": false
}
```

By default this adds a dataset to the current workspace. Set `replace_existing` to `true` only when you want to clear the current workspace datasets before importing the staged file.

## `DELETE /api/workbooks/<workbook_id>`

Removes one imported dataset from the current user's workspace and rebuilds the semantic index for the remaining datasets.

## `POST /api/query`

```json
{
  "question": "Which open cases look similar to motor vibration?"
}
```

Returns an answer, route, sources, and compact debug metadata.

`route` can be `count`, `status`, `sql`, `semantic`, `hybrid`, `multi`, `python`, or `no_dataset`.

When debug metadata is present, `debug.route_plan` includes the primary route, ordered candidates, strategy source, confidence, and execution mode. `multi` uses candidates as subroutes; other routes use candidates as fallbacks.
