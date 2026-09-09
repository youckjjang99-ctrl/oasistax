"""Small, fail-closed JSON transactions shared by the local CRM stores."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class CrmStorageError(RuntimeError):
    """Safe to show to a user; never includes file contents or OS errors."""


class CrmConflictError(CrmStorageError):
    pass


_LOCKS: dict[tuple[int, str], threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_HELD = threading.local()


@contextmanager
def locked_json(path: Path, *, timeout: float = 10.0) -> Iterator[None]:
    """Lock a stable sidecar across threads/processes, allowing nested callers.

    The sidecar must not be removed: deleting it could give two processes
    different lock inodes for the same JSON destination.
    """
    path = Path(path)
    # Resolve the parent, not the JSON itself: opening a Windows destination
    # merely to canonicalize its path can briefly interfere with replacement.
    key = (os.getpid(), os.path.normcase(str(path.parent.resolve() / path.name)))
    with _LOCKS_GUARD:
        local_lock = _LOCKS.setdefault(key, threading.RLock())
    deadline = time.monotonic() + max(0.0, timeout)
    if not local_lock.acquire(timeout=max(0.0, timeout)):
        raise CrmStorageError("다른 저장 작업이 진행 중입니다. 잠시 후 다시 저장해 주세요.")
    held = getattr(_HELD, "paths", None)
    if held is None:
        held = _HELD.paths = set()
    handle = None
    acquired = False
    try:
        if key in held:
            yield
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(path.with_name(path.name + ".lock"), "a+b")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise CrmStorageError(
                            "다른 저장 작업이 진행 중입니다. 잠시 후 다시 저장해 주세요."
                        ) from None
                    time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        except OSError:
            raise CrmStorageError("CRM 저장 공간에 접근하지 못했습니다. 원본은 변경하지 않았습니다.") from None
        held.add(key)
        yield
    finally:
        try:
            if acquired:
                held.discard(key)
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
            elif handle is not None:
                handle.close()
        finally:
            local_lock.release()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError("invalid_json_constant")


def read_json_object(path: Path) -> dict[str, Any]:
    """Only a missing file is empty; corruption/permissions must stop writes."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError):
        raise CrmStorageError("CRM 자료를 안전하게 읽지 못했습니다. 원본을 보존하고 저장을 중단했습니다.") from None
    if not isinstance(value, dict):
        raise CrmStorageError("CRM 자료 형식이 올바르지 않아 원본을 보존하고 저장을 중단했습니다.")
    return value


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Tolerate short Windows scanner/indexer handles without dropping a save."""
    deadline = time.monotonic() + 1.0
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if (
                os.name != "nt"
                or getattr(exc, "winerror", None) not in {5, 32, 33}
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(0.02)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Use inside locked_json after a strict read of the current document."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(temporary, "x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, path)
    except (OSError, TypeError, ValueError):
        raise CrmStorageError("CRM 저장을 완료하지 못했습니다. 기존 원본은 보존되었습니다.") from None
    finally:
        temporary.unlink(missing_ok=True)
