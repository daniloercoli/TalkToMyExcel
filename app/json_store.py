from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Callable, TypeVar


T = TypeVar("T")


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    with lock_path.open("a+b") as lock_file:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows only
            import msvcrt

            lock_file.seek(0)
            if not lock_file.read(1):
                lock_file.seek(0)
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def read_json(path: Path, default: T) -> T:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return deepcopy(default)


def read_or_create_json(
    path: Path,
    default: T,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
) -> T:
    with _file_lock(path):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        value = deepcopy(default)
        _atomic_write_json(path, value, indent=indent, sort_keys=sort_keys)
        return value


def write_json(
    path: Path,
    value: object,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
) -> None:
    with _file_lock(path):
        _atomic_write_json(path, value, indent=indent, sort_keys=sort_keys)


def update_json(
    path: Path,
    default: T,
    transform: Callable[[T], T],
    *,
    indent: int | None = None,
    sort_keys: bool = False,
    recover_invalid: bool = False,
) -> T:
    with _file_lock(path):
        try:
            current = read_json(path, default)
        except json.JSONDecodeError:
            if not recover_invalid:
                raise
            current = deepcopy(default)
        updated = transform(current)
        _atomic_write_json(path, updated, indent=indent, sort_keys=sort_keys)
        return updated


def delete_json(path: Path) -> None:
    with _file_lock(path):
        path.unlink(missing_ok=True)


def _atomic_write_json(
    path: Path,
    value: object,
    *,
    indent: int | None,
    sort_keys: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary_file:
            json.dump(
                value,
                temporary_file,
                ensure_ascii=False,
                indent=indent,
                sort_keys=sort_keys,
            )
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
