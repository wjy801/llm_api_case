from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import threading
import time
from typing import BinaryIO, Iterator

from .contracts import FlakyStoreError


_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def canonical_database_path(database_path: Path) -> str:
    resolved = str(database_path.resolve(strict=False))
    return os.path.normcase(resolved)


def writer_lock_path(database_path: Path) -> Path:
    canonical = canonical_database_path(database_path)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return database_path.parent / f".{database_path.name}.{digest}.writer.lock"


@contextmanager
def database_writer_lock(
    database_path: Path,
    *,
    timeout_ms: int,
) -> Iterator[Path]:
    canonical = canonical_database_path(database_path)
    with _PROCESS_LOCKS_GUARD:
        process_lock = _PROCESS_LOCKS.setdefault(canonical, threading.Lock())
    timeout_seconds = timeout_ms / 1000
    if not process_lock.acquire(timeout=timeout_seconds):
        raise FlakyStoreError(
            "db_writer_lock_timeout",
            "timed out waiting for the database writer lock",
        )
    handle: BinaryIO | None = None
    lock_path = writer_lock_path(database_path)
    deadline = time.monotonic() + timeout_seconds
    try:
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        while True:
            try:
                _try_lock(handle)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise FlakyStoreError(
                        "db_writer_lock_timeout",
                        "timed out waiting for the database writer lock",
                    )
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        try:
            yield lock_path
        finally:
            _unlock(handle)
    finally:
        if handle is not None:
            handle.close()
        process_lock.release()


if os.name == "nt":
    import msvcrt

    def _try_lock(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
