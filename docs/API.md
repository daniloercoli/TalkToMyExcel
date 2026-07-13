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

Importing a dataset clears the current user's conversation history.

## `DELETE /api/workbooks/<workbook_id>`

Removes one imported dataset from the current user's workspace and deletes only that dataset's semantic-index entries.

## `POST /api/semantic-index/rebuild`

Rebuilds the full semantic index for the active workspace. Use this maintenance endpoint when the index must be realigned with the imported datasets.

## `POST /api/query`

```json
{
  "question": "Which open cases look similar to motor vibration?"
}
```

Returns an answer, route, sources, and compact debug metadata.

Recent conversation history is supplied automatically. Follow-up questions can therefore refer to the preceding exchange without resending it in the request body.

`route` can be `count`, `status`, `sql`, `semantic`, `hybrid`, `multi`, `python`, or `no_dataset`.

When debug metadata is present, `debug.route_plan` includes the primary route, ordered candidates, strategy source, confidence, and execution mode. `multi` uses candidates as subroutes; other routes use candidates as fallbacks.

A valid route that finds no matching rows returns an empty-result answer. Fallbacks are used for technical failures or unsupported routes, not to reinterpret a valid zero-result query.

## `GET /api/session/context`

Returns saved-history size plus an estimate of the latest LLM payload, including `chars`, `estimated_tokens`, `messages`, `percentage`, and `source`.

## `POST /api/session/clear`

Clears the current user's conversation history and saved payload estimate. Returns:

```json
{"ok": true}
```
