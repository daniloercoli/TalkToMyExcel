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

The static public website lives in [public-site](public-site) and is configured
for:

```text
https://talktomyexcel.ercoliconsulting.eu/
```

It follows the same bilingual structure used by the RAGuardian public site:

- `public-site/index.html` redirects visitors to Italian or English.
- `public-site/it/` contains the Italian pages.
- `public-site/en/` contains the English pages.
- `public-site/css/` and `public-site/assets/` contain shared styling and media.

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

The sandbox image is required by the running app. It is used to profile uploaded
files and to run generated Python analysis in a short-lived Docker container,
separate from the Flask server process.

The standard build above starts from `python:3.11-slim` and installs the sandbox
dependencies declared in `Dockerfile.sandbox` (`pandas`, `openpyxl`, `xlrd`, and
`pyarrow`). Use this command unless you have a specific reason to reuse an
already prepared base image:

```bash
docker build -f Dockerfile.sandbox -t talktomyexcel-sandbox:latest .
```

Advanced variant:

```bash
docker build -f Dockerfile.sandbox \
  --build-arg SANDBOX_BASE=code-interpreter:latest \
  --build-arg INSTALL_SANDBOX_DEPS=0 \
  -t talktomyexcel-sandbox:latest .
```

This variant exists for deployments that already maintain a compatible sandbox
base image, such as `code-interpreter:latest`, with the required Python data
libraries preinstalled. `SANDBOX_BASE` swaps the base image, and
`INSTALL_SANDBOX_DEPS=0` skips reinstalling those dependencies during this
project's image build. If that base image is not available, or if you are setting
up TalkToMyExcel normally, use the standard build.

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
