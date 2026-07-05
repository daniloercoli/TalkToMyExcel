from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import docker

from app.config import Config
from app.logging_config import log
from app.sandbox import SandboxError
from app.stores import Workspace
from app.workbook import quote_ident


def run_python_analysis(workspace: Workspace, metadata: dict, code: str, request_id: str = "") -> dict:
    runs_dir = workspace.workbook_dir / "python-runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="run-", dir=runs_dir) as run_dir_name:
        run_dir = Path(run_dir_name)
        input_dir = run_dir / "input"
        output_dir = run_dir / "output"
        input_dir.mkdir()
        output_dir.mkdir()
        output_dir.chmod(0o777)
        code_path = input_dir / "analysis.py"
        result_path = output_dir / "result.json"
        code_path.write_text(code, encoding="utf-8")
        manifest = export_workbook_inputs(workspace, metadata, input_dir)

        log.info(
            "python_sandbox_start",
            extra={"request_id": request_id, "workspace_id": workspace.workspace_id, "tables": len(manifest["tables"])},
        )
        client = docker.from_env()
        container = None
        try:
            container = client.containers.run(
                Config.PYTHON_SANDBOX_IMAGE,
                entrypoint=["python", "/sandbox/python_runner.py"],
                command=["/input/analysis.py", "/output/result.json"],
                remove=True,
                network_disabled=True,
                read_only=True,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=128m"},
                user="nobody",
                mem_limit=Config.PYTHON_SANDBOX_MEMORY,
                volumes={
                    str(input_dir): {"bind": "/input", "mode": "ro"},
                    str(output_dir): {"bind": "/output", "mode": "rw"},
                },
                detach=True,
                stdout=True,
                stderr=True,
            )
            wait = container.wait(timeout=Config.PYTHON_SANDBOX_TIMEOUT)
            if int(wait.get("StatusCode", 1)) != 0:
                logs = container.logs(stdout=True, stderr=True).decode(errors="replace")
                raise SandboxError(logs.strip() or "Python sandbox failed")
        except docker.errors.ImageNotFound as exc:
            raise SandboxError(
                f"Python sandbox image not found: {Config.PYTHON_SANDBOX_IMAGE}. Build it with Dockerfile.sandbox."
            ) from exc
        except Exception:
            if container is not None:
                try:
                    container.kill()
                except Exception:
                    pass
            log.exception("python_sandbox_failed", extra={"request_id": request_id})
            raise

        if not result_path.exists():
            raise SandboxError("Python sandbox did not produce result.json")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        result["tables"] = manifest["tables"]
        log.info(
            "python_sandbox_done",
            extra={"request_id": request_id, "workspace_id": workspace.workspace_id, "ok": bool(result.get("ok"))},
        )
        return result


def export_workbook_inputs(workspace: Workspace, metadata: dict, input_dir: Path) -> dict:
    import duckdb

    conn = duckdb.connect(str(workspace.duckdb_path), read_only=True)
    tables = []
    for table in metadata["tables"]:
        csv_name = f"{table['table']}.csv"
        csv_path = input_dir / csv_name
        conn.execute(
            f"COPY (SELECT * FROM {quote_ident(table['table'])}) TO {quote_literal(str(csv_path))} (HEADER, DELIMITER ',')"
        )
        tables.append(
            {
                "workbook_id": table.get("workbook_id"),
                "filename": table.get("filename"),
                "sheet": table["sheet"],
                "table": table["table"],
                "csv": csv_name,
                "rows": table["rows"],
                "columns": table["columns"],
            }
        )
    conn.close()
    manifest = {
        "datasets": [
            {"id": dataset.get("id"), "filename": dataset.get("filename")}
            for dataset in metadata.get("datasets", [])
        ],
        "tables": tables,
    }
    if len(manifest["datasets"]) == 1:
        manifest["filename"] = manifest["datasets"][0]["filename"]
    (input_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
