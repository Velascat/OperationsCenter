# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for DependencyDriftCollector."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.observer.collectors.dependency_drift import DependencyDriftCollector
from operations_center.observer.models import DependencyDriftSignal
from operations_center.observer.service import ObserverContext


def _make_context(tmp_path: Path) -> ObserverContext:
    """Create a minimal ObserverContext with report_root pointing at *tmp_path*."""
    settings = MagicMock()
    settings.report_root = tmp_path
    return ObserverContext(
        repo_path=tmp_path,
        repo_name="test-repo",
        base_branch="main",
        run_id="obs_test_001",
        observed_at=datetime.now(UTC),
        source_command="test",
        settings=settings,
        commit_limit=10,
        hotspot_window=30,
        todo_limit=20,
        logs_root=tmp_path / "logs",
    )


class TestDependencyDriftCollector:
    def test_not_available_when_no_report_files(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert isinstance(signal, DependencyDriftSignal)
        assert signal.status == "not_available"

    def test_valid_report_with_actionable_statuses(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        data = {
            "statuses": [
                {"package": "requests", "notes": "outdated by 2 major versions"},
                {"package": "flask", "notes": "security patch available"},
                {"package": "numpy"},  # no notes → not actionable
            ],
            "created_task_ids": ["TASK-1", "TASK-2"],
        }
        (run_dir / "dependency_report.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert "actionable_statuses=2" in signal.summary
        assert "created_task_ids=2" in signal.summary

    def test_report_with_no_statuses_key(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        data = {"some_other_key": "value"}
        (run_dir / "dependency_report.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert "no statuses" in signal.summary

    def test_report_with_empty_statuses_list(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        data = {"statuses": []}
        (run_dir / "dependency_report.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert "no statuses" in signal.summary

    def test_multiple_report_dirs_picks_latest(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "run_old"
        old_dir.mkdir()
        old_report = old_dir / "dependency_report.json"
        old_report.write_text(json.dumps({"statuses": [{"package": "old", "notes": "x"}]}))
        os.utime(old_report, (1000, 1000))

        new_dir = tmp_path / "run_new"
        new_dir.mkdir()
        new_report = new_dir / "dependency_report.json"
        new_report.write_text(json.dumps({"statuses": [{"package": "new", "notes": "y"}]}))
        os.utime(new_report, (2000, 2000))

        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"
        assert "run_new" in signal.source

    def test_malformed_json(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        (run_dir / "dependency_report.json").write_text("not valid json {{{")
        ctx = _make_context(tmp_path)
        with pytest.raises(json.JSONDecodeError):
            DependencyDriftCollector().collect(ctx)
