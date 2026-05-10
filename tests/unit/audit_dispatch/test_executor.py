# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for Phase 6 subprocess executor.

Uses real Python subprocesses for behavioral tests.
Uses monkeypatching for unit-level tests that should not spawn processes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch


from operations_center.audit_dispatch.executor import ManagedAuditExecutor, ProcessResult
from operations_center.audit_toolset import ManagedAuditInvocationRequest

_RUN_ID = "example_managed_repo_audit_type_1_20260426T120000Z_aabb1122"

_MINIMAL_INVOCATION = {
    "repo_id": "example_managed_repo",
    "audit_type": "audit_type_1",
    "run_id": _RUN_ID,
    "working_directory": ".",
    "command": f"{sys.executable} -c 'import sys; sys.exit(0)'",
    "env": {"AUDIT_RUN_ID": _RUN_ID},
    "expected_output_dir": "output",
    "metadata": {},
}


def _make_invocation(**overrides) -> ManagedAuditInvocationRequest:
    data = {**_MINIMAL_INVOCATION, **overrides}
    return ManagedAuditInvocationRequest.model_validate(data)


class TestProcessResultProperties:
    def test_succeeded_true_on_exit_0(self) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        result = ProcessResult(
            exit_code=0,
            stdout_path=Path("/tmp/stdout.log"),
            stderr_path=Path("/tmp/stderr.log"),
            started_at=now,
            ended_at=now,
        )
        assert result.succeeded is True

    def test_succeeded_false_on_nonzero_exit(self) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        result = ProcessResult(
            exit_code=1,
            stdout_path=Path("/tmp/stdout.log"),
            stderr_path=Path("/tmp/stderr.log"),
            started_at=now,
            ended_at=now,
        )
        assert result.succeeded is False

    def test_succeeded_false_when_timed_out(self) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        result = ProcessResult(
            exit_code=None,
            stdout_path=Path("/tmp/stdout.log"),
            stderr_path=Path("/tmp/stderr.log"),
            started_at=now,
            ended_at=now,
            timed_out=True,
        )
        assert result.succeeded is False


class TestExecutorLogDir:
    def test_log_dir_created(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs" / "run1"
        executor = ManagedAuditExecutor(log_dir)
        inv = _make_invocation(working_directory=str(tmp_path))
        executor.execute(inv)
        assert log_dir.exists()

    def test_stdout_log_created(self, tmp_path: Path) -> None:
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(working_directory=str(tmp_path))
        result = executor.execute(inv)
        assert result.stdout_path.exists()

    def test_stderr_log_created(self, tmp_path: Path) -> None:
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(working_directory=str(tmp_path))
        result = executor.execute(inv)
        assert result.stderr_path.exists()


class TestExecutorExitCode:
    def test_exit_code_0(self, tmp_path: Path) -> None:
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            command=f"{sys.executable} -c 'import sys; sys.exit(0)'",
        )
        result = executor.execute(inv)
        assert result.exit_code == 0
        assert result.succeeded is True

    def test_exit_code_1(self, tmp_path: Path) -> None:
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            command=f"{sys.executable} -c 'import sys; sys.exit(1)'",
        )
        result = executor.execute(inv)
        assert result.exit_code == 1
        assert result.succeeded is False

    def test_exit_code_42(self, tmp_path: Path) -> None:
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            command=f"{sys.executable} -c 'import sys; sys.exit(42)'",
        )
        result = executor.execute(inv)
        assert result.exit_code == 42


class TestExecutorOutputCapture:
    def test_stdout_captured_to_log_file(self, tmp_path: Path) -> None:
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            command=f"{sys.executable} -c 'print(\"hello stdout\")'",
        )
        result = executor.execute(inv)
        content = result.stdout_path.read_text(encoding="utf-8")
        assert "hello stdout" in content

    def test_stderr_captured_to_log_file(self, tmp_path: Path) -> None:
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            command=f"{sys.executable} -c 'import sys; print(\"hello stderr\", file=sys.stderr)'",
        )
        result = executor.execute(inv)
        content = result.stderr_path.read_text(encoding="utf-8")
        assert "hello stderr" in content


class TestExecutorShellFalse:
    def test_subprocess_popen_called_with_list_args(self, tmp_path: Path) -> None:
        """Verify shell=False by asserting args is a list and shell kwarg is absent."""
        calls = []

        original_popen = subprocess.Popen

        class CapturingPopen(original_popen):
            def __init__(self, args, **kwargs):
                calls.append({"args": args, "shell": kwargs.get("shell", False)})
                super().__init__(args, **kwargs)

        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(working_directory=str(tmp_path))

        with patch("operations_center.audit_dispatch.executor.subprocess.Popen", CapturingPopen):
            executor.execute(inv)

        assert calls, "Popen was not called"
        assert calls[0]["shell"] is False
        assert isinstance(calls[0]["args"], list)

    def test_command_split_correctly(self, tmp_path: Path) -> None:
        """Multi-word command is split into argument list (not passed as single string)."""
        calls = []

        original_popen = subprocess.Popen

        class CapturingPopen(original_popen):
            def __init__(self, args, **kwargs):
                calls.append(args)
                super().__init__(args, **kwargs)

        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            command=f"{sys.executable} -c 'pass'",
        )

        with patch("operations_center.audit_dispatch.executor.subprocess.Popen", CapturingPopen):
            executor.execute(inv)

        assert len(calls[0]) > 1, "Command should be split into multiple args"


class TestExecutorTimeout:
    def test_timeout_sets_timed_out(self, tmp_path: Path) -> None:
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            command=f"{sys.executable} -c 'import time; time.sleep(60)'",
        )
        result = executor.execute(inv, timeout_seconds=0.2)
        assert result.timed_out is True

    def test_timeout_sets_error_message(self, tmp_path: Path) -> None:
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            command=f"{sys.executable} -c 'import time; time.sleep(60)'",
        )
        result = executor.execute(inv, timeout_seconds=0.2)
        assert result.error is not None
        assert "timed out" in result.error.lower()


class TestExecutorCwdOverride:
    def test_cwd_override_used(self, tmp_path: Path) -> None:
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(working_directory=str(tmp_path))
        # Run in other_dir — if it works without error, the cwd was accepted
        result = executor.execute(inv, cwd_override=str(other_dir))
        assert result.exit_code == 0


class TestExecutorTiming:
    def test_started_at_before_ended_at(self, tmp_path: Path) -> None:
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(working_directory=str(tmp_path))
        result = executor.execute(inv)
        assert result.started_at <= result.ended_at

    def test_duration_seconds_non_negative(self, tmp_path: Path) -> None:
        executor = ManagedAuditExecutor(tmp_path)
        inv = _make_invocation(working_directory=str(tmp_path))
        result = executor.execute(inv)
        assert result.duration_seconds >= 0
