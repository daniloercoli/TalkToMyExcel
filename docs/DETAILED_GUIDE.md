# Detailed Guide

## Architecture

TalkToMyExcel keeps the application small:

- Flask renders the UI and exposes JSON endpoints.
- Docker reads user-uploaded tabular files, returns safe CSV extracts plus metadata, and runs generated Python analysis when needed.
- DuckDB stores the imported datasets for each user workspace.
- Chroma stores embeddings for selected text columns.
- Regolo.ai is the recommended OpenAI-compatible provider for chat and embeddings.

The server never imports Pandas or OpenPyXL to read user files. File profiling and extraction happen in the sandbox image.

## Users and Workspaces

Each user gets an isolated workspace:

```text
app/data/workspaces/<user_id>/
app/uploads/workspaces/<user_id>/
```

Each user can keep multiple imported datasets in the same workspace. Uploading a new file is safe during staging. Importing with `replace_existing=false` adds the file to the workspace; importing with `replace_existing=true` clears the current datasets first. Individual datasets can be removed without deleting the rest of the workspace.

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
7. The Chroma collection is rebuilt across the active workspace datasets.

## Query Flow

The query engine uses simple routing:

- Exact status/serial questions use DuckDB.
- Count questions use DuckDB.
- Similar-problem questions use embeddings.
- Hybrid questions filter structured rows and then apply semantic search.
- Advanced numeric, diff, missing-ID, and multi-step calculation questions can use generated Python in a short-lived Docker sandbox.

The LLM receives compact, cited rows. It is not used as the database.

For Python analysis, the application exports the active DuckDB workspace tables to temporary CSV files, mounts them read-only at `/input`, runs generated Python with network disabled, and reads `/output/result.json`. The manifest includes dataset filenames so generated code can compare files. The container is removed after the run.

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

If Regolo.ai calls fail, verify `REGOLO_API_KEY` and selected model names in Settings.
