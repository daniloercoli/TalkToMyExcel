#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import ensure_dirs  # noqa: E402
from app.demo import cleanup_expired_demo_users  # noqa: E402


def main() -> int:
    ensure_dirs()
    deleted = cleanup_expired_demo_users()
    print(f"Deleted {deleted} expired demo user(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
