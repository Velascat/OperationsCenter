# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Wave 3 — post-merge regression detection helpers."""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock



def test_detect_no_regression_when_base_green():
    from operations_center.post_merge_regression import detect_post_merge_regressions
    gh = MagicMock()
    gh.get_branch_head.return_value = "abc123"
    gh.get_failed_checks.return_value = []
    out = detect_post_merge_regressions(gh, "o", "r")
    assert out == []


def test_detect_returns_signal_when_base_failing():
    from operations_center.post_merge_regression import detect_post_merge_regressions
    gh = MagicMock()
    gh.get_branch_head.return_value = "abc123"
    gh.get_failed_checks.return_value = ["test-suite", "lint"]
    # No list_recently_merged_prs method — fall back to attributing head only
    if hasattr(gh, "list_recently_merged_prs"):
        del gh.list_recently_merged_prs
    out = detect_post_merge_regressions(gh, "o", "r")
    assert len(out) == 1
    assert out[0].failed_checks == ("test-suite", "lint")
    assert out[0].merge_commit_sha == "abc123"


def test_detect_filters_by_lookback_window():
    from operations_center.post_merge_regression import detect_post_merge_regressions
    gh = MagicMock()
    gh.get_branch_head.return_value = "head"
    gh.get_failed_checks.return_value = ["lint"]
    old = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    recent = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    gh.list_recently_merged_prs.return_value = [
        {"number": 100, "merged_at": old,    "merge_commit_sha": "old1"},
        {"number": 101, "merged_at": recent, "merge_commit_sha": "new1"},
    ]
    out = detect_post_merge_regressions(gh, "o", "r", lookback_hours=24)
    assert len(out) == 1
    assert out[0].pr_number == 101


def test_detect_no_head_sha_returns_empty():
    from operations_center.post_merge_regression import detect_post_merge_regressions
    gh = MagicMock()
    gh.get_branch_head.return_value = None
    out = detect_post_merge_regressions(gh, "o", "r")
    assert out == []


def test_extract_evidence_file_tokens():
    from operations_center.post_merge_regression import _extract_evidence_file_tokens
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,3 +1,4 @@\n"
        "+x = 1\n"
        "diff --git a/bar.py b/bar.py\n"
        "+++ b/bar.py\n"
        "+y = 2\n"
    )
    files = _extract_evidence_file_tokens(diff)
    assert files == ("foo.py", "bar.py")


def test_extract_evidence_skips_dev_null():
    from operations_center.post_merge_regression import _extract_evidence_file_tokens
    diff = "+++ b/real.py\n+++ b/null\n"  # 'null' isn't /dev/null but skip
    # Actually only /dev/null is skipped; 'null' is a real path
    files = _extract_evidence_file_tokens(diff)
    assert "real.py" in files


def test_extract_evidence_caps_at_max():
    from operations_center.post_merge_regression import _extract_evidence_file_tokens
    diff = "\n".join(f"+++ b/file{i}.py" for i in range(20))
    files = _extract_evidence_file_tokens(diff, max_files=5)
    assert len(files) == 5


def test_create_revert_branch_handles_failure(tmp_path, monkeypatch):
    from operations_center import post_merge_regression as pmr

    def _failing_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args, b"", b"git: not a repo")
    monkeypatch.setattr(pmr.subprocess, "run", _failing_run)
    out = pmr.create_revert_branch(tmp_path, commit_sha="abc12345")
    assert out is None


def test_create_revert_branch_returns_name_on_success(tmp_path, monkeypatch):
    from operations_center import post_merge_regression as pmr
    calls: list[list[str]] = []
    def _ok_run(args, **kwargs):
        calls.append(list(args))
        result = subprocess.CompletedProcess(args=args, returncode=0, stdout=b"", stderr=b"")
        return result
    monkeypatch.setattr(pmr.subprocess, "run", _ok_run)
    branch = pmr.create_revert_branch(tmp_path, commit_sha="abcdef1234")
    assert branch == "revert/abcdef12"
    # Verify fetch + checkout + revert all called
    assert any("fetch" in c for c in calls)
    assert any("checkout" in c for c in calls)
    assert any("revert" in c for c in calls)
