from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import docker
from werkzeug.utils import secure_filename

from app.config import Config
from app.logging_config import log


ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv", "tsv", "parquet"}


class SandboxError(RuntimeError):
    pass


def save_upload(file_storage, workspace_upload_dir: Path) -> tuple[str, Path]:
    filename = secure_filename(file_storage.filename or "")
    if not filename or "." not in filename:
        raise ValueError("Invalid file name")
    extension = filename.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported file type")
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > Config.MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"File is larger than {Config.MAX_UPLOAD_MB} MB")

    staging_id = uuid.uuid4().hex
    staging_dir = workspace_upload_dir / "staging" / staging_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    path = staging_dir / filename
    file_storage.save(path)
    return staging_id, path


def profile_in_sandbox(input_path: Path, output_dir: Path, request_id: str = "") -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    client = docker.from_env()
    log.info(
        "sandbox_profile_start",
        extra={"request_id": request_id, "input": input_path.name, "output_dir": str(output_dir)},
    )
    try:
        container = client.containers.run(
            Config.SANDBOX_IMAGE,
            command=[f"/input/{input_path.name}", "/output", "/output/result.json"],
            remove=True,
            network_disabled=True,
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=128m"},
            user="nobody",
            mem_limit="768m",
            volumes={
                str(input_path.parent): {"bind": "/input", "mode": "ro"},
                str(output_dir): {"bind": "/output", "mode": "rw"},
            },
            detach=True,
            stdout=True,
            stderr=True,
        )
        wait = container.wait(timeout=Config.SANDBOX_TIMEOUT)
        if int(wait.get("StatusCode", 1)) != 0:
            logs = container.logs(stdout=True, stderr=True).decode(errors="replace")
            raise SandboxError(logs.strip() or "Sandbox failed")
    except docker.errors.ImageNotFound as exc:
        raise SandboxError(
            f"Sandbox image not found: {Config.SANDBOX_IMAGE}. Build it with Dockerfile.sandbox."
        ) from exc
    except Exception:
        log.exception("sandbox_profile_failed", extra={"request_id": request_id})
        raise

    if not result_path.exists():
        raise SandboxError("Sandbox did not produce result.json")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise SandboxError(result.get("error") or "Sandbox failed")
    log.info("sandbox_profile_done", extra={"request_id": request_id, "sheets": len(result["profile"]["sheets"])})
    return result["profile"]
