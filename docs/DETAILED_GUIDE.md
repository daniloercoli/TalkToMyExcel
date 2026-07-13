# Detailed Guide

## Architecture

TalkToMyExcel keeps the application small:

- Flask renders the UI and exposes JSON endpoints.
- Docker reads user-uploaded tabular files, returns safe CSV extracts plus metadata, and runs generated Python analysis when needed.
- DuckDB stores the imported datasets for each user workspace.
- Chroma stores embeddings for selected text columns.
- A query router selects or combines exact, semantic, and sandboxed analysis paths.
- Per-user conversation history supports contextual follow-up questions.
- Regolo.ai is the recommended OpenAI-compatible provider for chat and embeddings.

The server never imports Pandas or OpenPyXL to read user files. File profiling and extraction happen in the sandbox image.

## Sandbox Image

The running app needs a Docker image named by `SANDBOX_IMAGE`. By default,
`.env.example` sets both `SANDBOX_IMAGE` and `PYTHON_SANDBOX_IMAGE` to:

```env
talktomyexcel-sandbox:latest
```

That single image is used for two isolated jobs:

- File profiling and extraction during upload staging.
- Generated Python analysis for advanced calculation questions.

The normal build is:

```bash
docker build -f Dockerfile.sandbox -t talktomyexcel-sandbox:latest .
```

This starts from `python:3.11-slim` and installs the sandbox data libraries
declared in `Dockerfile.sandbox`: `pandas`, `openpyxl`, `xlrd`, and `pyarrow`.
Use this build for a regular local setup or a regular server deployment.

There is also an advanced build variant:

```bash
docker build -f Dockerfile.sandbox \
  --build-arg SANDBOX_BASE=code-interpreter:latest \
  --build-arg INSTALL_SANDBOX_DEPS=0 \
  -t talktomyexcel-sandbox:latest .
```

This exists only for environments that already maintain a compatible sandbox
base image with the required Python data libraries preinstalled. `SANDBOX_BASE`
selects that base image, while `INSTALL_SANDBOX_DEPS=0` avoids reinstalling the
same dependencies inside this project build. If the base image is not available,
or if you are not deliberately managing a shared sandbox base image, use the
normal build.

## Users and Workspaces

Each user gets an isolated workspace:

```text
app/data/workspaces/<user_id>/
app/uploads/workspaces/<user_id>/
```

Each user can keep multiple imported datasets in the same workspace. Uploading a new file is safe during staging. Importing with `replace_existing=false` adds the file to the workspace; importing with `replace_existing=true` clears the current datasets first. Individual datasets can be removed without deleting the rest of the workspace.

Conversation history and the last payload estimate are also stored per user. Importing a dataset clears that history so a new data context cannot accidentally reuse an old conversation.

## Provider Settings

The default config lives in `app/default_providers.json`.

Regolo.ai is preconfigured:

```env
REGOLO_API_KEY=...
```

The admin can choose:

- Chat provider/model
- Embedding provider/model

Local embeddings are supported through `sentence-transformers`. They keep semantic text local but need a compatible PyTorch runtime.

## File Flow

1. `POST /api/staging`
2. Server saves the upload to workspace staging storage.
3. Docker profiles the file and writes normalized CSV outputs.
4. UI shows sheets, preview rows, and suggested semantic columns.
5. `POST /api/workbooks` imports selected sheets and columns.
6. DuckDB stores the imported tables with dataset-scoped names.
7. Chroma adds semantic documents only for the newly imported dataset. Removing a dataset deletes only its documents.

A full semantic-index rebuild remains available from the UI and through `POST /api/semantic-index/rebuild` for maintenance or recovery.

## Query Flow

The query engine uses routed spreadsheet tools:

- Exact status/serial questions use DuckDB.
- Broad count questions use deterministic DuckDB summaries.
- Exact filters, dates, aggregates, and explicit group-by questions use read-only SQL.
- Similar-problem questions use embeddings.
- Hybrid questions filter structured rows and then apply semantic search.
- Multi-intent questions can combine SQL and semantic evidence before final synthesis.
- Advanced numeric, diff, missing-ID, and multi-step calculation questions can use generated Python in a short-lived Docker sandbox.
- Follow-up questions such as "same, but only open" use recent conversation context to resolve the current request before routing.

The LLM receives compact, cited rows. It is not used as the database.

A technically valid query that returns no rows is treated as a final empty result. Fallback routes are reserved for technical failures or unsupported paths, avoiding a different interpretation of a valid zero-result query.

For Python analysis, the application exports the active DuckDB workspace tables to temporary CSV files, mounts them read-only at `/input`, runs generated Python with network disabled, and reads `/output/result.json`. The manifest includes dataset filenames so generated code can compare files. The container is removed after the run.

See [routing.md](routing.md) for route details and [evaluation.md](evaluation.md) for the private golden Q/A workflow.

## Conversation Controls

The application keeps at most 20 recent messages per user. Routing uses the most recent exchange only when the current question contains a follow-up reference; answer generation can use the retained conversation.

The UI shows an estimated context percentage based on the last LLM payload when available. The clear button removes both saved history and the payload estimate through `POST /api/session/clear`.

## Optional Demo Sessions

Set these values to expose the temporary demo button on the login page:

```env
DEMO_ENABLED=true
DEMO_TIMEOUT_MINUTES=30
```

Each demo visitor gets an isolated, initially empty workspace. Expired demo users and their workspace files are removed on later demo activity; production deployments should also schedule `python scripts/cleanup_demo_users.py` so cleanup does not depend on new visitors.

## Logs

Set:

```env
LOG_LEVEL=DEBUG
LOG_FORMAT=json
LOG_DIR=app/logs
```

Useful fields include `request_id`, `user_id`, `workspace_id`, `staging_id`, `workbook_id`, and selected query route.

## Troubleshooting

If upload profiling fails, check Docker:

```bash
docker images | grep talktomyexcel-sandbox
docker build -f Dockerfile.sandbox -t talktomyexcel-sandbox:latest .
```

Use the advanced `SANDBOX_BASE` / `INSTALL_SANDBOX_DEPS=0` build only when the
chosen base image already contains `pandas`, `openpyxl`, `xlrd`, and `pyarrow`.
Otherwise the sandbox worker will not be able to read uploaded Excel, CSV, TSV,
or Parquet files.

If Regolo.ai calls fail, verify `REGOLO_API_KEY` and selected model names in Settings.
