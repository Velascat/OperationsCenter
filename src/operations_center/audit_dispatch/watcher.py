# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""In-flight ``run_status.json`` polling for managed audit dispatch (Phase 6, Slice F).

Used by the ``operations-center-audit watch`` CLI to observe an in-progress
managed audit's lifecycle in real time. Polling-based — no ``watchdog``
dependency added to the runtime.

The audit's run_status.json lives at ``<bucket_dir>/run_status.json`` where
the bucket directory's name encodes the ``run_id`` as a suffix (matching VF's
report-naming convention). This module finds the bucket by scanning the
expected output dir for a child directory whose name contains ``run_id``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from operations_center.audit_contracts.vocabulary import RunStatus


_TERMINAL_STATUSES = {
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.INTERRUPTED.value,
}


@dataclass(frozen=True, slots=True)
class RunStatusSnapshot:
    """One observed transition of ``run_status.json`` content."""

    path: Path
    status: str
    current_phase: str | None
    raw: dict
    is_terminal: bool


def _find_bucket_dir(expected_output_dir: Path, run_id: str) -> Path | None:
    """Locate the per-run bucket directory whose name contains ``run_id``."""
    if not expected_output_dir.is_dir():
        return None
    for child in sorted(expected_output_dir.iterdir(), reverse=True):
        if child.is_dir() and run_id in child.name:
            return child
    return None


def _read_status(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def poll_run_status(
    expected_output_dir: Path,
    run_id: str,
    *,
    poll_interval_s: float = 2.0,
    timeout_s: float | None = None,
) -> Iterator[RunStatusSnapshot]:
    """Yield each time the bucket's ``run_status.json`` content changes.

    Stops yielding when the most-recent snapshot's ``status`` is terminal
    (``completed``, ``failed``, ``interrupted``) or when ``timeout_s`` elapses.

    Yields nothing if the bucket dir never appears within the timeout.
    """
    deadline = time.monotonic() + timeout_s if timeout_s is not None else None
    last_payload: dict | None = None

    while True:
        bucket = _find_bucket_dir(expected_output_dir, run_id)
        if bucket is not None:
            status_path = bucket / "run_status.json"
            payload = _read_status(status_path)
            if payload is not None and payload != last_payload:
                last_payload = payload
                status = str(payload.get("status", "unknown"))
                snapshot = RunStatusSnapshot(
                    path=status_path,
                    status=status,
                    current_phase=payload.get("current_phase"),
                    raw=payload,
                    is_terminal=status in _TERMINAL_STATUSES,
                )
                yield snapshot
                if snapshot.is_terminal:
                    return

        if deadline is not None and time.monotonic() >= deadline:
            return
        time.sleep(poll_interval_s)


__all__ = ["RunStatusSnapshot", "poll_run_status"]
