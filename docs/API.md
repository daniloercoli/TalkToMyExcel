# API

These endpoints back the TalkToMyExcel web UI. They are session-cookie APIs,
not a bearer-token API intended for third-party integrations.

## Authentication and response formats

Log in through `POST /login` using HTML form fields `email` and `password`, then
send the returned Flask session cookie on API requests. Unauthenticated requests
are redirected to `/login` with HTTP `302`; they do not return a JSON `401`.
Use HTTPS outside local development.

Except for upload staging, request bodies are JSON and should use
`Content-Type: application/json`. Successful API responses are JSON. Handled
application errors normally have this shape:

```json
{"error": "Description"}
```

Common statuses are:

- `200`: request completed.
- `302`: no valid login session; follow or handle the `/login` redirect.
- `400`: missing/invalid input, unsupported file, failed import, or rebuild
  without active data.
- `404`: the requested workbook dataset does not exist.
- `500`: query execution failed unexpectedly.

Malformed JSON can be rejected by Flask before the endpoint handler and may use
Flask's default error body. Clients should check both the HTTP status and the
response `Content-Type` before decoding JSON.

## `POST /api/staging`

Content type: `multipart/form-data`. Upload field: `file`.

Accepted extensions are `.xlsx`, `.xls`, `.csv`, `.tsv`, and `.parquet`. The
application rejects files larger than `MAX_UPLOAD_MB` (50 MB by default).

Returns a staging ID, a profile containing sheets/tables, columns and preview
rows, plus current active-workspace metadata:

```json
{
  "staging_id": "...",
  "profile": {"filename": "example.xlsx", "sheets": []},
  "active": null
}
```

Staging does not import data into the active workspace.

## `GET /api/workbooks/active`

No request body. Returns the active workspace metadata for the logged-in user,
including imported datasets and their DuckDB tables:

```json
{"active": null}
```

`active` is `null` when the workspace has no imported dataset.

## `POST /api/workbooks`

JSON body:

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

By default this adds a dataset to the current workspace. Set
`replace_existing` to `true` only when the current datasets should be cleared
before importing the staged file. At least one valid sheet/table must be
selected. A successful import returns `{"active": {...}}` and clears the
current user's conversation history.

## `DELETE /api/workbooks/<workbook_id>`

No request body. Removes one imported dataset from the current user's workspace
and deletes that dataset's semantic-index entries. Returns updated active
metadata; `active` is `null` after deleting the last dataset. An unknown ID
returns `404`.

## `POST /api/semantic-index/rebuild`

No request body. Rebuilds the full semantic index for the active workspace and
returns:

```json
{"ok": true}
```

This rebuild is mandatory after changing the embedding provider/model,
`SEMANTIC_CHUNK_SIZE`, or `SEMANTIC_CHUNK_OVERLAP`. A workspace without active
data returns `400`.

## `POST /api/query`

JSON body:

```json
{
  "question": "Which open cases look similar to motor vibration?"
}
```

`question` must contain at least three characters after trimming. Recent
conversation history is supplied automatically; clients do not resend it.

The response contains `answer`, `route`, `sources`, and compact debug metadata.
`route` can be `count`, `status`, `sql`, `semantic`, `hybrid`, `multi`, `python`,
or `no_dataset`. `sources` can be empty for aggregate, count, SQL, or generated
Python results; it is not a guarantee that every answer has row citations.

When present, `debug.route_plan` includes the primary route, ordered candidates,
strategy source, confidence, and execution mode. `multi` uses candidates as
subroutes; other routes use candidates as fallbacks. A valid route with no
matching rows returns an empty-result answer. Fallbacks are for technical
failures or unsupported routes, not for reinterpreting a valid zero-row result.

## `GET /api/session/context`

No request body. Returns saved-history size plus an estimate of the latest LLM
payload:

```json
{
  "chars": 1234,
  "estimated_tokens": 309,
  "history_chars": 620,
  "max_chars": 50000,
  "messages": 4,
  "payload_messages": 6,
  "percentage": 2.468,
  "source": "last_llm_payload"
}
```

The estimate is informational, not provider billing data.

## `POST /api/session/clear`

No request body. Clears the current user's conversation history and saved
payload estimate. Returns:

```json
{"ok": true}
```
