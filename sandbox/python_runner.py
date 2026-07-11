from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
from pathlib import Path


MAX_TEXT = 12000


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python_runner.py <code-file> <result-json>", file=sys.stderr)
        return 2

    code_path = Path(sys.argv[1])
    result_json = Path(sys.argv[2])
    stdout = io.StringIO()
    stderr = io.StringIO()
    namespace = {"__name__": "__analysis__", "__file__": str(code_path)}

    try:
        code = code_path.read_text(encoding="utf-8")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compile(code, str(code_path), "exec"), namespace)
        result = {
            "ok": True,
            "answer": normalize(namespace.get("answer", namespace.get("result", ""))),
            "stdout": trim(stdout.getvalue()),
            "stderr": trim(stderr.getvalue()),
        }
    except Exception:
        result = {
            "ok": False,
            "answer": "",
            "stdout": trim(stdout.getvalue()),
            "stderr": trim(stderr.getvalue()),
            "error": trim(traceback.format_exc()),
        }

    result_json.parent.mkdir(parents=True, exist_ok=True)
    result_json.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


def normalize(value):
    if value is None:
        return ""
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict(orient="records")
        except TypeError:
            return value.to_dict()
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def trim(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n...[truncated]"


if __name__ == "__main__":
    raise SystemExit(main())
