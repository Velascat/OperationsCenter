# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the Wave 1 helpers introduced from autonomy_gaps.md phantoms.

Each function is small and pure (or a thin shim around an existing call)
so tests focus on edge cases that would otherwise re-introduce the gap.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


# ── _count_quality_suppressions ──────────────────────────────────────────────

def test_quality_suppressions_empty_diff():
    from operations_center.observer.collectors.quality_suppressions import (
        _count_quality_suppressions,
    )
    out = _count_quality_suppressions("")
    assert out.total == 0
    assert out.by_kind == {}


def test_quality_suppressions_counts_added_lines_only():
    """Suppressions in REMOVED lines (- prefix) don't count."""
    from operations_center.observer.collectors.quality_suppressions import (
        _count_quality_suppressions,
    )
    diff = (
        "+def foo():  # noqa: E501\n"
        "-def bar():  # noqa: E501\n"      # removal — must not count
        " def baz():\n"
        "+    pass  # type: ignore\n"
    )
    out = _count_quality_suppressions(diff)
    assert out.total == 2
    # Both kinds counted
    assert sum(out.by_kind.values()) == 2


def test_quality_suppressions_excludes_diff_header():
    """`+++ b/foo.py` headers must not match the +-prefix regex."""
    from operations_center.observer.collectors.quality_suppressions import (
        _count_quality_suppressions,
    )
    diff = (
        "--- a/foo.py\n"
        "+++ b/foo.py  # noqa\n"          # header — `++` should be skipped
        "+    pass  # noqa\n"
    )
    out = _count_quality_suppressions(diff)
    assert out.total == 1   # only the body line


def test_quality_suppressions_each_kind():
    from operations_center.observer.collectors.quality_suppressions import (
        _count_quality_suppressions,
    )
    diff = (
        "+x = 1  # noqa\n"
        "+y = 2  # type: ignore\n"
        "+@pytest.mark.skip\n"
        "+@pytest.mark.xfail\n"
        "+# pragma: no cover\n"
        "+# pylint: disable=foo\n"
    )
    out = _count_quality_suppressions(diff)
    assert out.total == 6


# ── _check_pr_description_quality ────────────────────────────────────────────

def test_pr_quality_empty_body_fails():
    from operations_center.adapters.pr_quality import _check_pr_description_quality
    out = _check_pr_description_quality(None)
    assert not out.ok
    assert out.score == 0.0
    assert "empty_body" in out.reasons


def test_pr_quality_short_body_fails():
    from operations_center.adapters.pr_quality import _check_pr_description_quality
    out = _check_pr_description_quality("fix")
    assert not out.ok
    assert "body_too_short" in " ".join(out.reasons)


def test_pr_quality_well_formed_passes():
    from operations_center.adapters.pr_quality import _check_pr_description_quality
    body = (
        "## Goal\n"
        "Add coverage for the dependency_drift collector.\n\n"
        "## Changes\n"
        "Adds three new test cases covering edge inputs.\n"
        "Tests run in isolation against a tmp_path fixture.\n"
    )
    out = _check_pr_description_quality(body)
    assert out.ok
    assert out.score >= 0.7


def test_pr_quality_diff_only_body_fails():
    """A body that's just an embedded diff with no prose — fails."""
    from operations_center.adapters.pr_quality import _check_pr_description_quality
    body = (
        "## Diff\n"
        "```\n"
        "+++ a/foo.py\n"
        "+def bar(): pass\n"
        "```\n"
    )
    out = _check_pr_description_quality(body)
    # Has section + length, but no prose — score in the middle, not great
    assert "no_prose_explanation" in out.reasons


# ── _in_maintenance_window ───────────────────────────────────────────────────

def _window(start_hour, end_hour, days=()):
    return SimpleNamespace(start_hour=start_hour, end_hour=end_hour, days=list(days))


def test_maintenance_window_no_windows_configured():
    from operations_center.maintenance_windows import _in_maintenance_window
    settings = SimpleNamespace(maintenance_windows=[])
    assert not _in_maintenance_window(settings, datetime(2026, 1, 1, 12, 0, tzinfo=UTC))


def test_maintenance_window_simple_match():
    from operations_center.maintenance_windows import _in_maintenance_window
    s = SimpleNamespace(maintenance_windows=[_window(2, 6)])
    assert _in_maintenance_window(s, datetime(2026, 1, 1, 4, 0, tzinfo=UTC))
    assert not _in_maintenance_window(s, datetime(2026, 1, 1, 8, 0, tzinfo=UTC))


def test_maintenance_window_wraps_midnight():
    from operations_center.maintenance_windows import _in_maintenance_window
    # 22:00 → 04:00 next day
    s = SimpleNamespace(maintenance_windows=[_window(22, 4)])
    assert _in_maintenance_window(s, datetime(2026, 1, 1, 23, 0, tzinfo=UTC))
    assert _in_maintenance_window(s, datetime(2026, 1, 1, 2,  0, tzinfo=UTC))
    assert not _in_maintenance_window(s, datetime(2026, 1, 1, 12, 0, tzinfo=UTC))


def test_maintenance_window_weekday_gate():
    from operations_center.maintenance_windows import _in_maintenance_window
    s = SimpleNamespace(maintenance_windows=[_window(0, 23, days=[5, 6])])  # weekends only
    assert _in_maintenance_window(s, datetime(2026, 1, 3, 12, 0, tzinfo=UTC))   # Sat
    assert not _in_maintenance_window(s, datetime(2026, 1, 5, 12, 0, tzinfo=UTC))  # Mon


def test_maintenance_window_defensive_against_missing_fields():
    from operations_center.maintenance_windows import _in_maintenance_window
    s = SimpleNamespace(maintenance_windows=[SimpleNamespace()])  # no fields at all
    # Defaults to start=0, end=0 → never in window
    assert not _in_maintenance_window(s, datetime(2026, 1, 1, 12, 0, tzinfo=UTC))


# ── _get_kodo_version + _is_quota_exhausted_result are thin shims ────────────

def test_get_kodo_version_returns_none_for_missing_binary():
    from operations_center.adapters.kodo.adapter import _get_kodo_version
    assert _get_kodo_version("no-such-binary-anywhere") is None


def test_is_quota_exhausted_result_recognises_quota_phrases():
    from operations_center.adapters.kodo.adapter import (
        KodoRunResult,
        _is_quota_exhausted_result,
    )
    quota_hit = KodoRunResult(
        exit_code=1,
        stdout="",
        stderr="You've exceeded your usage limit. upgrade your plan",
        command=[],
    )
    assert _is_quota_exhausted_result(quota_hit)

    benign = KodoRunResult(exit_code=0, stdout="ok", stderr="", command=[])
    assert not _is_quota_exhausted_result(benign)
