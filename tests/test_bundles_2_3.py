"""Tests for the C9 detector + baseline_validation."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from operations_center.contracts.enums import ValidationStatus


# ── C9 detector ──────────────────────────────────────────────────────────────

def test_c9_skips_known_values(tmp_path):
    """Status enums like 'in progress' are well-known — don't flag."""
    from operations_center.entrypoints.code_health_audit.main import (
        CodeContext, _detect_c9_doc_value_drift,
    )
    src = tmp_path / "src"
    docs = tmp_path / "docs" / "design"
    docs.mkdir(parents=True)
    (docs / "x.md").write_text(
        "## Plane states\n"
        "Status can be `in progress` or `done`.\n"
    )
    src.mkdir()
    (src / "x.py").write_text("# nothing useful\n")
    ctx = CodeContext(src_root=src, tests_root=tmp_path / "t", repo_root=tmp_path)
    count, _ = _detect_c9_doc_value_drift(ctx)
    assert count == 0


def test_c9_flags_truly_drifted_value(tmp_path):
    """A doc value with no string-literal anchor is flagged."""
    from operations_center.entrypoints.code_health_audit.main import (
        CodeContext, _detect_c9_doc_value_drift,
    )
    src = tmp_path / "src"
    docs = tmp_path / "docs" / "design"
    docs.mkdir(parents=True)
    (docs / "x.md").write_text(
        "## Whatever\n"
        "The kind can be `nonexistent_kind_value`.\n"
    )
    src.mkdir()
    (src / "x.py").write_text("# real code, no such literal\n")
    ctx = CodeContext(src_root=src, tests_root=tmp_path / "t", repo_root=tmp_path)
    count, samples = _detect_c9_doc_value_drift(ctx)
    assert count == 1
    assert "nonexistent_kind_value" in samples[0]


def test_c9_accepts_pydantic_field_definition(tmp_path):
    """A doc citing a value that's also a Pydantic field name passes."""
    from operations_center.entrypoints.code_health_audit.main import (
        CodeContext, _detect_c9_doc_value_drift,
    )
    src = tmp_path / "src"
    docs = tmp_path / "docs" / "design"
    docs.mkdir(parents=True)
    (docs / "x.md").write_text(
        "## Schema\n"
        "Each task has a `is_compliant` value.\n"
    )
    src.mkdir()
    (src / "x.py").write_text("class Foo:\n    is_compliant: bool = False\n")
    ctx = CodeContext(src_root=src, tests_root=tmp_path / "t", repo_root=tmp_path)
    count, _ = _detect_c9_doc_value_drift(ctx)
    assert count == 0


def test_c9_accepts_function_definition(tmp_path):
    """When a value-cited token is actually a function name, accept."""
    from operations_center.entrypoints.code_health_audit.main import (
        CodeContext, _detect_c9_doc_value_drift,
    )
    src = tmp_path / "src"
    docs = tmp_path / "docs" / "design"
    docs.mkdir(parents=True)
    (docs / "x.md").write_text(
        "## Helpers\n"
        "Status is set by `signal_stale`.\n"
    )
    src.mkdir()
    (src / "x.py").write_text("def signal_stale(): pass\n")
    ctx = CodeContext(src_root=src, tests_root=tmp_path / "t", repo_root=tmp_path)
    count, _ = _detect_c9_doc_value_drift(ctx)
    assert count == 0


# ── baseline_validation ──────────────────────────────────────────────────────

def _repo_cfg(commands=(), timeout=300, skip=False):
    return SimpleNamespace(
        validation_commands=list(commands),
        validation_timeout_seconds=timeout,
        skip_baseline_validation=skip,
    )


def test_baseline_skips_when_no_repo_cfg(tmp_path):
    from operations_center.execution.baseline_validation import run_baseline_validation
    out = run_baseline_validation(tmp_path, repo_cfg=None)
    assert out.status == ValidationStatus.SKIPPED


def test_baseline_skips_when_opt_out(tmp_path):
    from operations_center.execution.baseline_validation import run_baseline_validation
    out = run_baseline_validation(tmp_path, repo_cfg=_repo_cfg(["echo ok"], skip=True))
    assert out.status == ValidationStatus.SKIPPED


def test_baseline_skips_when_no_commands(tmp_path):
    from operations_center.execution.baseline_validation import run_baseline_validation
    out = run_baseline_validation(tmp_path, repo_cfg=_repo_cfg())
    assert out.status == ValidationStatus.SKIPPED


def test_baseline_passes_all_commands_succeed(tmp_path):
    from operations_center.execution.baseline_validation import run_baseline_validation
    out = run_baseline_validation(
        tmp_path, repo_cfg=_repo_cfg(["true", "echo ok"]),
    )
    assert out.status == ValidationStatus.PASSED
    assert out.commands_run == 2
    assert out.commands_passed == 2
    assert out.commands_failed == 0


def test_baseline_fails_first_failure(tmp_path):
    from operations_center.execution.baseline_validation import run_baseline_validation
    out = run_baseline_validation(
        tmp_path, repo_cfg=_repo_cfg(["true", "false", "echo skipped"]),
    )
    assert out.status == ValidationStatus.FAILED
    # Stops at the failing command — third one not run
    assert out.commands_run == 2
    assert out.commands_passed == 1
    assert out.commands_failed == 1


def test_baseline_reports_timeout(tmp_path, monkeypatch):
    from operations_center.execution import baseline_validation
    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=kw.get("timeout"))
    monkeypatch.setattr(baseline_validation.subprocess, "run", _timeout)
    out = baseline_validation.run_baseline_validation(
        tmp_path, repo_cfg=_repo_cfg(["sleep 999"], timeout=1),
    )
    # No TIMEOUT in the existing enum — environmental failure maps to ERROR.
    assert out.status == ValidationStatus.ERROR
    assert "timeout" in (out.failure_excerpt or "").lower()
