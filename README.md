# TalkToMyExcel

TalkToMyExcel is a self-hostable Flask app for asking questions over Excel,
CSV, TSV, Parquet, and other tabular business exports.
It uses a hybrid engine:

- DuckDB for exact lookups, filters, counts, and aggregations.
- Chroma for semantic search on the text columns selected during import.
- Regolo.ai as the recommended OpenAI-compatible LLM and embedding provider.
- Docker sandboxing for file profiling, extraction, and generated Python analysis.

The project is MIT licensed.

## Public Website

The public static website lives in [public-site](public-site) and is configured
for:

```text
https://talktomyexcel.ercoliconsulting.eu/
```

It presents the same product boundary as the application:

- Upload and import of tabular files: `.xlsx`, `.xls`, `.csv`, `.tsv`, `.parquet`.
- DuckDB for structured questions, Chroma for semantic text search.
- Per-user workspaces for imported datasets.
- Regolo.ai as the recommended provider for European, zero-retention AI usage.
- Static marketing/privacy pages only; the public website does not receive file uploads.

Publishing is handled by the GitHub Pages workflow in
[.github/workflows/pages.yml](.github/workflows/pages.yml). In the GitHub repo,
enable Pages with GitHub Actions as the source, then point the custom domain to
GitHub Pages. The site artifact includes [public-site/CNAME](public-site/CNAME).

## Clean Public Repo

To publish the code from a clean repository without carrying over this local
repository history, sync a snapshot into a separate directory:

```bash
scripts/sync-public-repo.sh --init /path/to/TalkToMyExcel-public
```

The sync copies tracked files plus non-ignored new files, and excludes local
secrets, runtime uploads, runtime data, logs, and this repository's `.git`
directory. Review the destination, then commit and push it to the new GitHub
repository.

## 5-Minute Start

Requirements:

- Python 3.11+
- Docker
- A Regolo.ai API key, unless you switch to local embeddings and another chat provider

```bash
git clone <your-fork-url> TalkToMyExcel
cd TalkToMyExcel

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

cp .env.example .env
# edit .env and set REGOLO_API_KEY

docker build -f Dockerfile.sandbox -t talktomyexcel-sandbox:latest .

python -m app.app
```

Build the sandbox with:

```bash
docker build -f Dockerfile.sandbox \
  --build-arg SANDBOX_BASE=code-interpreter:latest \
  --build-arg INSTALL_SANDBOX_DEPS=0 \
  -t talktomyexcel-sandbox:latest .
```

Open http://127.0.0.1:5001.

Default first admin comes from `.env`:

```env
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=change-me-now
```

Advanced calculation questions can use a short-lived Docker sandbox. The app exports the active DuckDB workspace tables to read-only CSV inputs, runs LLM-generated Python with networking disabled, reads the JSON result, and removes the container.

## How It Works

1. Upload an Excel, CSV, TSV, or Parquet file.
2. The file is profiled inside Docker, not in the main server process.
3. Pick sheets/tables and semantic columns.
4. Confirm import.
5. Add more files to the same workspace when needed.
6. Ask questions in English UI over the active workspace data.

Each imported file stays available as a dataset in the workspace. You can remove individual datasets from the UI, or use the API replace option when you intentionally want to clear the workspace and import a fresh file set.

## Production

```bash
gunicorn -c gunicorn.conf.py wsgi:application
```

See [docs/DETAILED_GUIDE.md](docs/DETAILED_GUIDE.md) for architecture, provider settings, and troubleshooting.
