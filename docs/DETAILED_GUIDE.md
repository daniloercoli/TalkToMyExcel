# Detailed Guide

## Architecture

TalkToMyExcel keeps the application small:

- Flask renders the UI and exposes JSON endpoints.
- Docker reads user-uploaded tabular files, returns safe CSV extracts plus metadata, and runs generated Python analysis when needed.
- DuckDB stores the imported datasets for each user workspace.
- Chroma stores embeddings for selected text columns.
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

The query engine uses routed spreadsheet tools:

- Exact status/serial questions use DuckDB.
- Broad count questions use deterministic DuckDB summaries.
- Exact filters, dates, aggregates, and explicit group-by questions use read-only SQL.
- Similar-problem questions use embeddings.
- Hybrid questions filter structured rows and then apply semantic search.
- Multi-intent questions can combine SQL and semantic evidence before final synthesis.
- Advanced numeric, diff, missing-ID, and multi-step calculation questions can use generated Python in a short-lived Docker sandbox.

The LLM receives compact, cited rows. It is not used as the database.

For Python analysis, the application exports the active DuckDB workspace tables to temporary CSV files, mounts them read-only at `/input`, runs generated Python with network disabled, and reads `/output/result.json`. The manifest includes dataset filenames so generated code can compare files. The container is removed after the run.

See [routing.md](routing.md) for route details and [evaluation.md](evaluation.md) for the private golden Q/A workflow.

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
