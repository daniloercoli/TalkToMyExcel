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

Built-in provider definitions live in `app/default_providers.json`. The active
selection and any custom definitions are stored in `app/data/settings.json`,
which is created on first start.

Regolo.ai is preconfigured:

```env
REGOLO_API_KEY=...
```

The admin can choose:

- Chat provider/model
- Embedding provider/model

Local embeddings are supported through `sentence-transformers`. They keep semantic text local but need a compatible PyTorch runtime.

To add an OpenAI-compatible endpoint, stop the application and add provider
definitions to `app/data/settings.json`. Keep secrets in environment variables,
not in this JSON file. For example:

```json
{
  "chat": {
    "provider": "acme-chat",
    "model": "chat-model",
    "temperature": 0.2
  },
  "embedding": {
    "provider": "acme-embedding",
    "model": "embedding-model"
  },
  "custom_llm_providers": [
    {
      "id": "acme-chat",
      "name": "Acme Chat",
      "type": "openai_compatible",
      "base_url": "https://api.example.com/v1",
      "api_key_env": "ACME_API_KEY",
      "requires_api_key": true,
      "models": ["chat-model"],
      "default_model": "chat-model"
    }
  ],
  "custom_embedding_providers": [
    {
      "id": "acme-embedding",
      "name": "Acme Embeddings",
      "type": "openai_compatible",
      "base_url": "https://api.example.com/v1",
      "api_key_env": "ACME_API_KEY",
      "requires_api_key": true,
      "models": ["embedding-model"],
      "default_model": "embedding-model"
    }
  ]
}
```

Then set `ACME_API_KEY` in `.env`, restart the application, and verify the
selection on the Settings page. Provider IDs must be unique across their own
chat or embedding list.

Changing only the chat provider or model affects later requests immediately.
Changing the embedding provider/model, `SEMANTIC_CHUNK_SIZE`, or
`SEMANTIC_CHUNK_OVERLAP` makes existing Chroma vectors incompatible with the
new configuration. Restart after environment changes and run a **full semantic
index rebuild before using semantic search**. Existing vectors are not migrated
automatically.

## File Flow

1. `POST /api/staging`
2. Server saves the upload to workspace staging storage.
3. Docker profiles the file and writes normalized CSV outputs.
4. UI shows sheets, preview rows, and suggested semantic columns.
5. `POST /api/workbooks` imports selected sheets and columns.
6. DuckDB stores the imported tables with dataset-scoped names.
7. Chroma adds semantic documents only for the newly imported dataset. Removing a dataset deletes only its documents.

A full semantic-index rebuild remains available from the UI and through `POST /api/semantic-index/rebuild`. It is mandatory after changing the embedding provider/model or semantic chunk settings, and is also useful for maintenance or recovery.

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

The chat model receives the question, compact schema/result context, and recent
conversation context when applicable. Routes tied to identifiable worksheet
rows can also return source references; aggregate and generated-analysis routes
may not. The chat model is not used as the database.

When remote embeddings are selected, the embedding endpoint receives the text
from semantic columns during import/rebuild and the user's semantic query at
search time. Select local embeddings if that text must not leave the host.

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

Each demo visitor gets an isolated, initially empty workspace. Expired demo
users and their workspace files are removed on later demo activity. Production
deployments should also schedule cleanup so it does not depend on new visitors.
Use the same virtual-environment interpreter and operating-system account as the
application. For an installation under `/srv/TalkToMyExcel`, a cron entry can be:

```cron
*/5 * * * * /srv/TalkToMyExcel/.venv/bin/python /srv/TalkToMyExcel/scripts/cleanup_demo_users.py >> /srv/TalkToMyExcel/app/logs/demo-cleanup.log 2>&1
```

Change `/srv/TalkToMyExcel` to the real absolute path. Run the command manually
once before installing the cron entry and check that it prints the number of
deleted users.

## Same-host Concurrency

Users, provider settings, and conversation sessions use atomic JSON replacement
plus inter-process file locks. This supports multiple Gunicorn workers sharing
one local data directory. Do not mount the same JSON files into several app
hosts and treat them as a distributed database; use a single application host
or migrate that state to a transactional database before horizontal scaling.

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
