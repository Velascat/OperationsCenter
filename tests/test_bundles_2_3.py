# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the C9 detector + baseline_validation."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace


from operations_center.contracts.enums import ValidationStatus


# ── K2 detector (doc value drift — superseded OC9) ───────────────────────────

def test_k2_skips_known_values(tmp_path):
    """Status enums like 'done' are well-known — don't flag."""
    from custodian.audit_kit.detector import AuditContext
    from custodian.audit_kit.detectors.docs import detect_k2
    src = tmp_path / "src"
    docs = tmp_path / "docs" / "design"
    docs.mkdir(parents=True)
    (docs / "x.md").write_text(
        "## Plane states\n"
        "Status can be `done` or `cancelled`.\n"
    )
    src.mkdir()
    (src / "x.py").write_text("# nothing useful\n")
    ctx = AuditContext(
        src_root=src, tests_root=tmp_path / "t", repo_root=tmp_path,
        config={}, plugin_modules=[],
    )
    result = detect_k2(ctx)
    assert result.count == 0


def test_k2_flags_truly_drifted_value(tmp_path):
    """A doc value with no string-literal anchor is flagged."""
    from custodian.audit_kit.detector import AuditContext
    from custodian.audit_kit.detectors.docs import detect_k2
    src = tmp_path / "src"
    docs = tmp_path / "docs" / "design"
    docs.mkdir(parents=True)
    (docs / "x.md").write_text(
        "## Whatever\n"
        "The kind can be `nonexistent_kind_value`.\n"
    )
    src.mkdir()
    (src / "x.py").write_text("# real code, no such literal\n")
    ctx = AuditContext(
        src_root=src, tests_root=tmp_path / "t", repo_root=tmp_path,
        config={}, plugin_modules=[],
    )
    result = detect_k2(ctx)
    assert result.count == 1
    assert "nonexistent_kind_value" in result.samples[0]


def test_k2_accepts_field_annotation(tmp_path):
    """A doc citing a token that's a type-annotated field name passes."""
    from custodian.audit_kit.detector import AuditContext
    from custodian.audit_kit.detectors.docs import detect_k2
    src = tmp_path / "src"
    docs = tmp_path / "docs" / "design"
    docs.mkdir(parents=True)
    (docs / "x.md").write_text(
        "## Schema\n"
        "Status can be `compliant` or `non_compliant`.\n"
    )
    src.mkdir()
    (src / "x.py").write_text("class Foo:\n    compliant: bool = False\n    non_compliant: bool = False\n")
    ctx = AuditContext(
        src_root=src, tests_root=tmp_path / "t", repo_root=tmp_path,
        config={}, plugin_modules=[],
    )
    result = detect_k2(ctx)
    assert result.count == 0


def test_k2_accepts_function_definition(tmp_path):
    """When a value-cited token is a function name, it passes."""
    from custodian.audit_kit.detector import AuditContext
    from custodian.audit_kit.detectors.docs import detect_k2
    src = tmp_path / "src"
    docs = tmp_path / "docs" / "design"
    docs.mkdir(parents=True)
    (docs / "x.md").write_text(
        "## Helpers\n"
        "Status is set by `signal_stale`.\n"
    )
    src.mkdir()
    (src / "x.py").write_text("def signal_stale(): pass\n")
    ctx = AuditContext(
        src_root=src, tests_root=tmp_path / "t", repo_root=tmp_path,
        config={}, plugin_modules=[],
    )
    result = detect_k2(ctx)
    assert result.count == 0


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
