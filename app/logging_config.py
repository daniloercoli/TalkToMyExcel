from __future__ import annotations

import json
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


SECRET_RE = re.compile(r"(api[_-]?key|authorization|token|secret|password)", re.I)


def mask(value: object) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:6]}...{text[-4:]}"


def safe_extra(extra: dict | None) -> dict:
    clean = {}
    for key, value in (extra or {}).items():
        clean[key] = mask(value) if SECRET_RE.search(str(key)) else value
    return clean


class JsonFormatter(logging.Formatter):
    skip = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message",
    }

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self.skip and not key.startswith("_"):
                data[key] = mask(value) if SECRET_RE.search(key) else value
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str, ensure_ascii=False)


def setup_logger(name: str = "talktomyexcel") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logger.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", "%H:%M:%S"))
    logger.addHandler(console)

    try:
        log_dir = Path(os.getenv("LOG_DIR", "app/logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "talktomyexcel.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(level)
        if os.getenv("LOG_FORMAT", "json").lower() == "json":
            file_handler.setFormatter(JsonFormatter())
        else:
            file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("File logging disabled; log directory is not writable")
    logger.propagate = False
    return logger


log = setup_logger()
