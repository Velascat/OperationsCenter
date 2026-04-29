# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Wave 2 — pre-execution validation helpers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ── _check_execution_environment ─────────────────────────────────────────────

def test_env_check_missing_workspace(tmp_path):
    from operations_center.execution.validation import _check_execution_environment
    out = _check_execution_environment(tmp_path / "does-not-exist")
    assert not out.ok
    assert "workspace_path" in out.missing


def test_env_check_empty_workspace(tmp_path):
    from operations_center.execution.validation import _check_execution_environment
    out = _check_execution_environment(tmp_path)
    assert ".git" in out.missing
    assert "workspace_is_empty" in out.notes


def test_env_check_populated_clone(tmp_path):
    from operations_center.execution.validation import _check_execution_environment
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]")
    out = _check_execution_environment(tmp_path, required_files=("pyproject.toml",))
    assert out.ok
    assert out.missing == ()


def test_env_check_required_file_missing(tmp_path):
    from operations_center.execution.validation import _check_execution_environment
    (tmp_path / ".git").mkdir()
    out = _check_execution_environment(tmp_path, required_files=("pyproject.toml",))
    assert not out.ok
    assert "pyproject.toml" in out.missing


# ── _collect_open_pr_files ───────────────────────────────────────────────────

def test_collect_open_pr_files_empty():
    from operations_center.execution.validation import _collect_open_pr_files
    gh = MagicMock()
    gh.list_open_prs.return_value = []
    out = _collect_open_pr_files(gh, "o", "r")
    assert out == {}


def test_collect_open_pr_files_excludes_one():
    from operations_center.execution.validation import _collect_open_pr_files
    gh = MagicMock()
    gh.list_open_prs.return_value = [{"number": 1}, {"number": 2}, {"number": 3}]
    gh.list_pr_files.side_effect = lambda o, r, n: [f"file_{n}.py"]
    out = _collect_open_pr_files(gh, "o", "r", exclude_pr=2)
    assert set(out.keys()) == {1, 3}


def test_collect_open_pr_files_silently_skips_failed_per_pr():
    from operations_center.execution.validation import _collect_open_pr_files
    gh = MagicMock()
    gh.list_open_prs.return_value = [{"number": 1}, {"number": 2}]
    def _files(o, r, n):
        if n == 1: raise RuntimeError("rate limit")
        return ["b.py"]
    gh.list_pr_files.side_effect = _files
    out = _collect_open_pr_files(gh, "o", "r")
    assert out == {2: ["b.py"]}


# ── _has_conflict_with_active_task ───────────────────────────────────────────

def test_conflict_no_overlap():
    from operations_center.execution.validation import _has_conflict_with_active_task
    has, prs = _has_conflict_with_active_task(["a.py"], {1: ["b.py"], 2: ["c.py"]})
    assert not has
    assert prs == []


def test_conflict_overlap_returns_pr_numbers():
    from operations_center.execution.validation import _has_conflict_with_active_task
    has, prs = _has_conflict_with_active_task(
        ["src/foo.py", "src/bar.py"],
        {1: ["src/baz.py"], 2: ["src/foo.py"], 3: ["src/bar.py"]},
    )
    assert has
    assert prs == [2, 3]


def test_conflict_excludes_in_review_pr():
    from operations_center.execution.validation import _has_conflict_with_active_task
    has, prs = _has_conflict_with_active_task(
        ["a.py"],
        {42: ["a.py"], 99: ["a.py"]},
        in_review_pr=42,
    )
    assert has
    assert prs == [99]


def test_conflict_empty_candidate():
    from operations_center.execution.validation import _has_conflict_with_active_task
    has, prs = _has_conflict_with_active_task([], {1: ["a.py"]})
    assert not has
    assert prs == []


# ── build_improve_triage_result ──────────────────────────────────────────────

def test_improve_triage_filters_invalid_suggestions(tmp_path):
    from operations_center.execution.validation import build_improve_triage_result
    out = build_improve_triage_result(
        success=True,
        summary="ok",
        suggestions=[{"title": "real"}, "not-a-dict", {}, {"title": "another"}],
        workspace_path=tmp_path,
        kodo_exit_code=0,
    )
    assert out.success
    assert len(out.suggestions) == 2
    assert all(isinstance(s, dict) for s in out.suggestions)


def test_improve_triage_truncates_summary(tmp_path):
    from operations_center.execution.validation import build_improve_triage_result
    out = build_improve_triage_result(
        success=False,
        summary="x" * 1000,
        suggestions=None,
        workspace_path=tmp_path,
        kodo_exit_code=1,
    )
    assert len(out.summary) == 500
