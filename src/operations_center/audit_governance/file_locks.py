# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""File-level locking for governance state files (Linux/macOS only).

Wraps fcntl.flock (POSIX) to provide exclusive, auto-released locks on state
files. Used by budgets.py and cooldowns.py to prevent concurrent write races.

PLATFORM NOTE: fcntl is available on Linux and macOS but NOT on Windows.
OperationsCenter is deployed on Linux; do not use this module on Windows hosts.
"""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


class FileLockTimeoutError(OSError):
    """Raised when the exclusive lock cannot be acquired within the timeout."""


@contextmanager
def locked_state_file(path: Path, timeout: float = 5.0) -> Generator[None, None, None]:
    """Acquire an exclusive lock on a .lock file adjacent to *path*, then yield.

    A separate <path>.lock sentinel is used so that locking never interferes
    with normal file reads/writes on the state path itself.

    Raises FileLockTimeoutError if the lock cannot be acquired within *timeout* seconds.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + timeout
    fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise FileLockTimeoutError(
                        f"Could not acquire lock on {lock_path} within {timeout}s"
                    ) from None
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


__all__ = ["FileLockTimeoutError", "locked_state_file"]
