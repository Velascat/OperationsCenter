# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 2 — verify the RxP wire round-trip inside the kodo invoker.

The mapper still produces a ``KodoPreparedRun`` (kodo-orchestration
shape with goal_text, validation_commands, kodo_mode). The invoker
constructs an RxP ``RuntimeInvocation`` to describe the subprocess
call, runs it, and converts the resulting ``RuntimeResult`` back to
``KodoRunCapture`` for kodo-specific normalization.

These tests pin the contract shape so Phase 3 can swap the underlying
runner for ``ExecutorRuntime.run(invocation)`` without changing the
public surface of the kodo backend.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from rxp.contracts import RuntimeInvocation, RuntimeResult

from operations_center.backends.kodo.invoke import (
    KodoBackendInvoker,
    _invoke_via_rxp,
)
from operations_center.backends.kodo.models import KodoPreparedRun
from operations_center.backends.kodo.runner import KodoRunResult


def _prepared(tmp_path: Path, **kw) -> KodoPreparedRun:
    defaults = dict(
        run_id="run-rxp-1",
        goal_text="Fix bug",
        constraints_text=None,
        repo_path=tmp_path / "repo",
        task_branch="auto/fix",
        goal_file_path=tmp_path / "repo" / ".kodo_goal.md",
        validation_commands=[],
        timeout_seconds=300,
    )
    defaults.update(kw)
    return KodoPreparedRun(**defaults)


def _mock_kodo(stdout: str = "", stderr: str = "", exit_code: int = 0):
    kodo = MagicMock()
    kodo.build_command.return_value = ["kodo", "--goal-file", "/tmp/g.md", "--project", "/repo"]
    kodo._run_subprocess.return_value = KodoRunResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr,
        command=["kodo", "--goal-file", "/tmp/g.md", "--project", "/repo"],
    )
    return kodo


class TestBuildInvocation:
    """The invoker's ``_build_invocation`` produces a valid RxP type."""

    def test_invocation_is_rxp_runtime_invocation(self, tmp_path):
        kodo = _mock_kodo()
        invoker = KodoBackendInvoker(kodo)
        prepared = _prepared(tmp_path)
        invocation = invoker._build_invocation(prepared)
        assert isinstance(invocation, RuntimeInvocation)

    def test_invocation_carries_runtime_metadata(self, tmp_path):
        kodo = _mock_kodo()
        prepared = _prepared(tmp_path)
        invocation = KodoBackendInvoker(kodo)._build_invocation(prepared)
        assert invocation.invocation_id == "run-rxp-1"
        assert invocation.runtime_name == "kodo"
        assert invocation.runtime_kind == "subprocess"
        assert invocation.command  # non-empty
        assert invocation.working_directory == str(prepared.repo_path)
        assert invocation.input_payload_path == str(prepared.goal_file_path)
        assert invocation.timeout_seconds == 300

    def test_invocation_metadata_carries_kodo_specifics(self, tmp_path):
        kodo = _mock_kodo()
        prepared = _prepared(tmp_path, kodo_mode="test", task_branch="my-branch")
        invocation = KodoBackendInvoker(kodo)._build_invocation(prepared)
        assert invocation.metadata["kodo_mode"] == "test"
        assert invocation.metadata["task_branch"] == "my-branch"
        # orchestrator_override is absent when prepared.orchestrator_override is None
        assert "orchestrator_override" not in invocation.metadata

    def test_orchestrator_override_lands_in_metadata(self, tmp_path):
        kodo = _mock_kodo()
        # When orchestrator_override is set, _build_invocation calls
        # settings.model_copy(...) to derive a per-call profile. Give it
        # a real object whose timeout_seconds is None so the timeout
        # comparison short-circuits cleanly.
        from types import SimpleNamespace
        kodo.settings.model_copy.return_value = SimpleNamespace(timeout_seconds=None)
        prepared = _prepared(tmp_path, orchestrator_override="claude-code:opus")
        invocation = KodoBackendInvoker(kodo)._build_invocation(prepared)
        assert invocation.metadata["orchestrator_override"] == "claude-code:opus"


class TestInvokeViaRxp:
    """``_invoke_via_rxp`` produces a valid RuntimeResult for any kodo run."""

    def test_succeeded_status_for_exit_zero(self, tmp_path):
        kodo = _mock_kodo(stdout="ok", exit_code=0)
        prepared = _prepared(tmp_path)
        invocation = KodoBackendInvoker(kodo)._build_invocation(prepared)
        rxp_result, _raw = _invoke_via_rxp(
            invocation, kodo, datetime.now(tz=timezone.utc),
        )
        assert isinstance(rxp_result, RuntimeResult)
        assert rxp_result.status == "succeeded"
        assert rxp_result.exit_code == 0
        assert rxp_result.invocation_id == invocation.invocation_id
        assert rxp_result.runtime_kind == "subprocess"

    def test_failed_status_for_nonzero_exit(self, tmp_path):
        kodo = _mock_kodo(stderr="boom", exit_code=1)
        prepared = _prepared(tmp_path)
        invocation = KodoBackendInvoker(kodo)._build_invocation(prepared)
        rxp_result, _raw = _invoke_via_rxp(
            invocation, kodo, datetime.now(tz=timezone.utc),
        )
        assert rxp_result.status == "failed"
        assert rxp_result.exit_code == 1

    def test_timed_out_status_when_stderr_carries_timeout_marker(self, tmp_path):
        kodo = _mock_kodo(
            stderr="\n[timeout: process group killed after 300s]", exit_code=-1,
        )
        prepared = _prepared(tmp_path)
        invocation = KodoBackendInvoker(kodo)._build_invocation(prepared)
        rxp_result, _raw = _invoke_via_rxp(
            invocation, kodo, datetime.now(tz=timezone.utc),
        )
        assert rxp_result.status == "timed_out"


class TestRoundTrip:
    """End-to-end ``invoke`` still returns a ``KodoRunCapture`` whose
    fields are sourced from the RxP RuntimeResult."""

    def test_full_invocation_returns_kodo_capture(self, tmp_path):
        kodo = _mock_kodo(stdout="hello", exit_code=0)
        prepared = _prepared(tmp_path)
        capture = KodoBackendInvoker(kodo).invoke(prepared)
        assert capture.run_id == "run-rxp-1"
        assert capture.exit_code == 0
        assert capture.stdout == "hello"
        assert capture.timeout_hit is False
        # Command on the capture is the same one carried by the RuntimeInvocation
        assert capture.command == ["kodo", "--goal-file", "/tmp/g.md", "--project", "/repo"]
