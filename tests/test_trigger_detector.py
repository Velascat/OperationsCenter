# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for the TriggerDetector: queue drain primary, drop file secondary."""
from __future__ import annotations

from pathlib import Path

from operations_center.spec_director.trigger import TriggerDetector
from operations_center.spec_director.models import TriggerSource


def _make_detector(tmp_path: Path) -> TriggerDetector:
    drop_file = tmp_path / "spec_direction.md"
    return TriggerDetector(drop_file_path=drop_file)


def test_no_trigger_when_active_campaign(tmp_path):
    detector = _make_detector(tmp_path)
    result = detector.detect(ready_count=0, running_count=0, has_active_campaign=True)
    assert result is None


def test_queue_drain_triggers_when_board_empty(tmp_path):
    detector = _make_detector(tmp_path)
    result = detector.detect(ready_count=0, running_count=0, has_active_campaign=False)
    assert result is not None
    assert result.source == TriggerSource.QUEUE_DRAIN


def test_queue_drain_does_not_trigger_if_running_tasks_exist(tmp_path):
    detector = _make_detector(tmp_path)
    result = detector.detect(ready_count=0, running_count=1, has_active_campaign=False)
    assert result is None


def test_queue_drain_does_not_trigger_if_ready_tasks_exist(tmp_path):
    detector = _make_detector(tmp_path)
    result = detector.detect(ready_count=2, running_count=0, has_active_campaign=False)
    assert result is None


def test_drop_file_takes_priority_over_queue_drain(tmp_path):
    detector = _make_detector(tmp_path)
    drop_file = tmp_path / "spec_direction.md"
    drop_file.write_text("Focus on auth module refactor")
    result = detector.detect(ready_count=0, running_count=0, has_active_campaign=False)
    assert result is not None
    assert result.source == TriggerSource.DROP_FILE
    assert "auth module" in result.seed_text


def test_drop_file_triggers_even_when_board_has_tasks(tmp_path):
    """Drop file (operator intent) fires regardless of board state."""
    detector = _make_detector(tmp_path)
    drop_file = tmp_path / "spec_direction.md"
    drop_file.write_text("Operator wants this")
    result = detector.detect(ready_count=5, running_count=2, has_active_campaign=False)
    assert result is not None
    assert result.source == TriggerSource.DROP_FILE


def test_archive_drop_file_moves_file(tmp_path):
    detector = _make_detector(tmp_path)
    drop_file = tmp_path / "spec_direction.md"
    drop_file.write_text("seed")
    detector.archive_drop_file()
    assert not drop_file.exists()
    archive_dir = tmp_path / "spec_direction.archive"
    assert archive_dir.exists()
    archived = list(archive_dir.iterdir())
    assert len(archived) == 1


def test_no_trigger_when_board_not_empty_and_no_drop_file(tmp_path):
    detector = _make_detector(tmp_path)
    result = detector.detect(ready_count=3, running_count=0, has_active_campaign=False)
    assert result is None
