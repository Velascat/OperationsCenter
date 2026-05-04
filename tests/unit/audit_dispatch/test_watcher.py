# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the in-flight run_status watcher (Phase 6, Slice F)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from operations_center.audit_dispatch.watcher import (
    RunStatusSnapshot,
    poll_run_status,
)


def _write_status(path: Path, status: str, current_phase: str = "running") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "contract_name": "managed-repo-audit",
                "producer": "videofoundry",
                "repo_id": "videofoundry",
                "run_id": "test_run_xyz",
                "audit_type": "representative",
                "status": status,
                "current_phase": current_phase,
            }
        ),
        encoding="utf-8",
    )


class TestPollRunStatus:
    def test_yields_terminal_snapshot_when_already_completed(self, tmp_path: Path) -> None:
        bucket = tmp_path / "bucket_test_run_xyz"
        bucket.mkdir()
        _write_status(bucket / "run_status.json", "completed", current_phase="completed")

        snapshots = list(
            poll_run_status(
                tmp_path,
                "test_run_xyz",
                poll_interval_s=0.05,
                timeout_s=1.0,
            )
        )
        assert len(snapshots) == 1
        assert snapshots[0].status == "completed"
        assert snapshots[0].is_terminal is True

    def test_yields_each_distinct_state_change(self, tmp_path: Path) -> None:
        bucket = tmp_path / "bucket_test_run_xyz"
        bucket.mkdir()
        status_path = bucket / "run_status.json"
        _write_status(status_path, "running", current_phase="bootstrap")

        snapshots: list[RunStatusSnapshot] = []

        def consumer() -> None:
            for s in poll_run_status(
                tmp_path,
                "test_run_xyz",
                poll_interval_s=0.05,
                timeout_s=3.0,
            ):
                snapshots.append(s)

        t = threading.Thread(target=consumer)
        t.start()

        time.sleep(0.2)
        _write_status(status_path, "running", current_phase="rendering")
        time.sleep(0.2)
        _write_status(status_path, "completed", current_phase="completed")
        t.join(timeout=5.0)

        statuses = [(s.status, s.current_phase) for s in snapshots]
        # Initial running observed first; rendering phase change; final completed.
        assert ("running", "bootstrap") in statuses
        assert ("completed", "completed") in statuses
        assert snapshots[-1].is_terminal is True

    def test_yields_nothing_when_bucket_never_appears(self, tmp_path: Path) -> None:
        snapshots = list(
            poll_run_status(
                tmp_path,
                "no_such_run",
                poll_interval_s=0.05,
                timeout_s=0.3,
            )
        )
        assert snapshots == []

    def test_finds_bucket_by_run_id_substring(self, tmp_path: Path) -> None:
        # Bucket name follows VF convention: "<channel>_<timestamp>_<run_id_hex>"
        bucket = tmp_path / "Connective_Contours_20260504_120000_test_run_xyz"
        bucket.mkdir()
        _write_status(bucket / "run_status.json", "completed")
        snapshots = list(
            poll_run_status(tmp_path, "test_run_xyz", poll_interval_s=0.05, timeout_s=1.0)
        )
        assert len(snapshots) == 1
