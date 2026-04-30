# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the periodic Plane task seeder."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.config.settings import ScheduledTask, Settings
from operations_center.scheduled_tasks.runner import (
    ScheduledTaskRunner,
    _is_due,
    _parse_at,
    _parse_every,
    _task_key,
    due_tasks,
)


# ── parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("30m",   30 * 60),
    ("6h",    6 * 3600),
    ("1d",    86400),
    ("1w",    7 * 86400),
    ("  2h ", 2 * 3600),
    ("3W",    3 * 7 * 86400),
])
def test_parse_every_valid(raw, expected):
    assert _parse_every(raw) == expected


@pytest.mark.parametrize("raw", ["", "1", "h", "1y", "abc", "1.5h"])
def test_parse_every_invalid(raw):
    with pytest.raises(ValueError):
        _parse_every(raw)


def test_parse_at_valid():
    assert _parse_at("09:00") == (9, 0)
    assert _parse_at("23:59") == (23, 59)
    assert _parse_at("00:00") == (0, 0)


@pytest.mark.parametrize("raw", ["", "9:00am", "25:00", "9:60", "abc"])
def test_parse_at_invalid(raw):
    with pytest.raises(ValueError):
        _parse_at(raw)


# ── due-check ────────────────────────────────────────────────────────────────

def _task(every="1d", at=None, on_days=None):
    return ScheduledTask(
        every=every, at=at, on_days=on_days,
        title="test", goal="g", repo_key="r", kind="goal",
    )


def test_is_due_first_run_with_no_at_or_days():
    """A task with no anchor fires the very first cycle (last_run is None)."""
    t = _task(every="1h")
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert _is_due(t, last_run=None, now=now)


def test_is_due_interval_gate():
    """Doesn't fire if interval hasn't elapsed."""
    t = _task(every="1d")
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert not _is_due(t, last_run=now - timedelta(hours=12), now=now)
    assert _is_due(t, last_run=now - timedelta(hours=25), now=now)


def test_is_due_at_anchor_within_slack():
    """Anchor matches when current time is within slack of HH:MM."""
    t = _task(every="1h", at="09:00")
    on_anchor   = datetime(2026, 1, 1, 9,  2,  tzinfo=UTC)   # 2 min late
    way_off     = datetime(2026, 1, 1, 14, 0,  tzinfo=UTC)
    assert _is_due(t, last_run=None, now=on_anchor)
    assert not _is_due(t, last_run=None, now=way_off)


def test_is_due_on_days_gate():
    """Weekday gate excludes off-days."""
    t = _task(every="1h", on_days=["mon"])
    monday    = datetime(2026, 1, 5,  10, 0, tzinfo=UTC)   # Jan 5 2026 is Mon
    tuesday   = datetime(2026, 1, 6,  10, 0, tzinfo=UTC)
    assert _is_due(t, last_run=None, now=monday)
    assert not _is_due(t, last_run=None, now=tuesday)


def test_is_due_combined_all_three():
    t = _task(every="1w", at="09:00", on_days=["mon"])
    last_run = datetime(2025, 12, 29, 9, 0, tzinfo=UTC)
    next_due = datetime(2026, 1, 5,  9, 1, tzinfo=UTC)  # next Mon, 1m after anchor
    assert _is_due(t, last_run=last_run, now=next_due)
    too_soon = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)   # < 1w later
    assert not _is_due(t, last_run=last_run, now=too_soon)


def test_is_due_malformed_every_skipped():
    t = _task(every="not-a-duration")
    assert not _is_due(t, last_run=None, now=datetime.now(UTC))


# ── due_tasks (state file integration) ───────────────────────────────────────

def _settings_with_tasks(tasks: list[ScheduledTask]) -> MagicMock:
    """Minimal Settings stub — only `.scheduled_tasks` is needed."""
    s = MagicMock(spec=Settings)
    s.scheduled_tasks = tasks
    return s


def test_due_tasks_no_state_file(tmp_path):
    settings = _settings_with_tasks([_task(every="1h")])
    state = tmp_path / "lr.json"
    out = due_tasks(settings, state_file=state)
    assert len(out) == 1


def test_due_tasks_respects_state(tmp_path):
    task = _task(every="1d")
    settings = _settings_with_tasks([task])
    state = tmp_path / "lr.json"
    # Write last_run = 1 hour ago — task NOT due yet.
    last_iso = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    state.write_text(json.dumps({_task_key(task): last_iso}), encoding="utf-8")
    out = due_tasks(settings, state_file=state)
    assert out == []


def test_due_tasks_state_file_corruption_treated_as_empty(tmp_path):
    settings = _settings_with_tasks([_task(every="1h")])
    state = tmp_path / "lr.json"
    state.write_text("not-json", encoding="utf-8")
    out = due_tasks(settings, state_file=state)
    assert len(out) == 1  # corruption → fire as if no history


# ── runner ───────────────────────────────────────────────────────────────────

def test_runner_creates_plane_task_and_persists_state(tmp_path):
    task = _task(every="1h")
    settings = _settings_with_tasks([task])
    plane = MagicMock()
    plane.create_issue.return_value = {"id": "abc-123"}

    state = tmp_path / "lr.json"
    runner = ScheduledTaskRunner(plane, settings, state_file=state)
    created = runner.tick()

    assert created == ["abc-123"]
    plane.create_issue.assert_called_once()
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert _task_key(task) in saved


def test_runner_does_not_persist_state_on_create_failure(tmp_path):
    task = _task(every="1h")
    settings = _settings_with_tasks([task])
    plane = MagicMock()
    plane.create_issue.side_effect = RuntimeError("plane down")

    state = tmp_path / "lr.json"
    runner = ScheduledTaskRunner(plane, settings, state_file=state)
    created = runner.tick()

    assert created == []
    assert not state.exists()  # no partial state — next cycle retries


def test_runner_includes_trusted_source_label(tmp_path):
    """Scheduled tasks must carry source: autonomy so policy doesn't review-block."""
    task = _task(every="1h")
    settings = _settings_with_tasks([task])
    plane = MagicMock()
    plane.create_issue.return_value = {"id": "x"}
    runner = ScheduledTaskRunner(plane, settings, state_file=tmp_path / "lr.json")
    runner.tick()
    labels = plane.create_issue.call_args.kwargs["label_names"]
    assert "source: autonomy" in labels
    assert "source: scheduled-task" in labels


def test_task_key_stable_across_runs():
    t1 = _task(every="1h")
    t2 = _task(every="1d")  # different `every`, same title+repo
    assert _task_key(t1) == _task_key(t2)
    t3 = _task(every="1h")
    t3.title = "different"
    assert _task_key(t3) != _task_key(t1)
