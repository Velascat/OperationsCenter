# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Hardening arc item 4 — artifact-path staleness checks at trace build.

When the temp directory holding an ExecutorRuntime stdout/stderr capture
is reaped between the run and the trace build, the trace must surface a
warning (not error) so an operator using ``operations-center-run-show``
sees the staleness up front instead of debugging a missing path.
"""

from __future__ import annotations

from pathlib import Path

from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ExecutionStatus, ValidationStatus
from operations_center.contracts.execution import ExecutionResult, RuntimeInvocationRef
from operations_center.observability.recorder import ExecutionRecorder
from operations_center.observability.trace import RunReportBuilder


def _result_with_paths(*, stdout: str, stderr: str, artifact_dir: str) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-stale-1",
        proposal_id="prop", decision_id="dec",
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        runtime_invocation_ref=RuntimeInvocationRef(
            invocation_id="iv-1",
            runtime_name="direct_local",
            runtime_kind="subprocess",
            stdout_path=stdout,
            stderr_path=stderr,
            artifact_directory=artifact_dir,
        ),
    )


def test_warning_emitted_when_stdout_path_missing(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "ad"; artifact_dir.mkdir()
    stdout = artifact_dir / "stdout.txt"; stdout.write_text("ok", encoding="utf-8")
    stderr = artifact_dir / "stderr.txt"  # never created
    result = _result_with_paths(
        stdout=str(stdout), stderr=str(stderr), artifact_dir=str(artifact_dir),
    )
    record = ExecutionRecorder().record(result, backend="direct_local", lane="aider_local")
    trace = RunReportBuilder().build_report(record)

    stale_warnings = [w for w in trace.warnings if "no longer exists on disk" in w]
    assert any("stderr_path" in w for w in stale_warnings)
    assert not any("stdout_path" in w for w in stale_warnings)
    assert not any("artifact_directory" in w for w in stale_warnings)


def test_warning_emitted_when_artifact_dir_reaped(tmp_path: Path) -> None:
    reaped = tmp_path / "gone"
    result = _result_with_paths(
        stdout=str(reaped / "stdout.txt"),
        stderr=str(reaped / "stderr.txt"),
        artifact_dir=str(reaped),
    )
    record = ExecutionRecorder().record(result, backend="direct_local", lane="aider_local")
    trace = RunReportBuilder().build_report(record)

    stale_warnings = [w for w in trace.warnings if "no longer exists on disk" in w]
    assert any("stdout_path" in w for w in stale_warnings)
    assert any("stderr_path" in w for w in stale_warnings)
    assert any("artifact_directory" in w for w in stale_warnings)


def test_no_staleness_warning_when_paths_resolve(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "ad"; artifact_dir.mkdir()
    stdout = artifact_dir / "stdout.txt"; stdout.write_text("ok", encoding="utf-8")
    stderr = artifact_dir / "stderr.txt"; stderr.write_text("", encoding="utf-8")
    result = _result_with_paths(
        stdout=str(stdout), stderr=str(stderr), artifact_dir=str(artifact_dir),
    )
    record = ExecutionRecorder().record(result, backend="direct_local", lane="aider_local")
    trace = RunReportBuilder().build_report(record)

    assert not any("no longer exists on disk" in w for w in trace.warnings)


def test_no_staleness_check_when_ref_absent(tmp_path: Path) -> None:
    """demo_stub-style results (no runtime_invocation_ref) must not warn."""
    result = ExecutionResult(
        run_id="r", proposal_id="p", decision_id="d",
        status=ExecutionStatus.SUCCEEDED, success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )
    record = ExecutionRecorder().record(result, backend="demo_stub", lane="aider_local")
    trace = RunReportBuilder().build_report(record)
    assert not any("no longer exists on disk" in w for w in trace.warnings)


def test_path_check_tolerates_oserror(monkeypatch, tmp_path: Path) -> None:
    """A permission-error / broken-symlink during exists() must not crash trace build."""
    from operations_center.observability import trace as trace_mod

    real_path_cls = trace_mod.Path

    class _ExplodingPath(real_path_cls):  # type: ignore[misc, valid-type]
        def exists(self) -> bool:  # type: ignore[override]
            raise OSError("simulated permission denied")

    monkeypatch.setattr(trace_mod, "Path", _ExplodingPath)

    result = _result_with_paths(
        stdout=str(tmp_path / "x.txt"),
        stderr=str(tmp_path / "y.txt"),
        artifact_dir=str(tmp_path),
    )
    record = ExecutionRecorder().record(result, backend="direct_local", lane="aider_local")
    trace = RunReportBuilder().build_report(record)
    # The wrapper swallows OSError and treats the path as not-present, so we
    # see staleness warnings instead of a crash.
    assert any("no longer exists on disk" in w for w in trace.warnings)
