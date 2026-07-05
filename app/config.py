from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


ROOT = Path(__file__).resolve().parents[1]
if load_dotenv:
    load_dotenv(ROOT / ".env")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    DATA_DIR = ROOT / "app" / "data"
    UPLOAD_DIR = ROOT / "app" / "uploads"
    LOG_DIR = ROOT / "app" / "logs"
    DEFAULT_PROVIDERS_FILE = ROOT / "app" / "default_providers.json"
    SETTINGS_FILE = DATA_DIR / "settings.json"
    USERS_FILE = DATA_DIR / "users.json"
    MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
    SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "talktomyexcel-sandbox:latest")
    SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "120"))
    PYTHON_SANDBOX_IMAGE = os.getenv("PYTHON_SANDBOX_IMAGE", SANDBOX_IMAGE)
    PYTHON_SANDBOX_TIMEOUT = int(os.getenv("PYTHON_SANDBOX_TIMEOUT", "20"))
    PYTHON_SANDBOX_MEMORY = os.getenv("PYTHON_SANDBOX_MEMORY", "768m")


def ensure_dirs() -> None:
    for path in (Config.DATA_DIR, Config.UPLOAD_DIR, Config.LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
