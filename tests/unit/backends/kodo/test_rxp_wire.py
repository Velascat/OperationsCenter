# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 2/3 — RxP wire pinning + ExecutorRuntime delegation.

These tests pin the contract shape between the kodo backend's invoker
and ExecutorRuntime:

  KodoPreparedRun → _build_invocation → RuntimeInvocation
  RuntimeInvocation → ExecutorRuntime.run → RuntimeResult
  RuntimeResult     → invoke() → KodoRunCapture
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from rxp.contracts import RuntimeInvocation, RuntimeResult

from operations_center.backends.kodo.invoke import KodoBackendInvoker
from operations_center.backends.kodo.models import KodoPreparedRun


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


def _mock_kodo():
    kodo = MagicMock()
    kodo.build_command.return_value = ["kodo", "--goal-file", "/tmp/g.md", "--project", "/repo"]
    return kodo


class _FakeRuntime:
    def __init__(self, *, status: str = "succeeded", exit_code: int = 0,
                 stdout: str = "", stderr: str = "") -> None:
        self.status = status
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.last_invocation: RuntimeInvocation | None = None

    def run(self, invocation: RuntimeInvocation) -> RuntimeResult:
        self.last_invocation = invocation
        ar = Path(invocation.artifact_directory) if invocation.artifact_directory else Path("/tmp")
        ar.mkdir(parents=True, exist_ok=True)
        sout = ar / "stdout.txt"
        serr = ar / "stderr.txt"
        sout.write_text(self.stdout, encoding="utf-8")
        serr.write_text(self.stderr, encoding="utf-8")
        now = datetime.now(timezone.utc).isoformat()
        return RuntimeResult(
            invocation_id=invocation.invocation_id,
            runtime_name=invocation.runtime_name,
            runtime_kind=invocation.runtime_kind,
            status=self.status,
            exit_code=self.exit_code,
            started_at=now,
            finished_at=now,
            stdout_path=str(sout),
            stderr_path=str(serr),
        )


class TestBuildInvocation:
    """``_build_invocation`` produces a valid RxP RuntimeInvocation."""

    def test_invocation_is_rxp_runtime_invocation(self, tmp_path):
        invoker = KodoBackendInvoker(_mock_kodo(), runtime=_FakeRuntime())
        invocation = invoker._build_invocation(_prepared(tmp_path))
        assert isinstance(invocation, RuntimeInvocation)

    def test_invocation_carries_runtime_metadata(self, tmp_path):
        kodo = _mock_kodo()
        prepared = _prepared(tmp_path)
        invocation = KodoBackendInvoker(kodo, runtime=_FakeRuntime())._build_invocation(prepared)
        assert invocation.invocation_id == "run-rxp-1"
        assert invocation.runtime_name == "kodo"
        assert invocation.runtime_kind == "subprocess"
        assert invocation.command  # non-empty
        assert invocation.working_directory == str(prepared.repo_path)
        assert invocation.input_payload_path == str(prepared.goal_file_path)
        assert invocation.timeout_seconds == 300
        assert invocation.artifact_directory  # tmp dir set per-invocation

    def test_invocation_metadata_carries_kodo_specifics(self, tmp_path):
        invoker = KodoBackendInvoker(_mock_kodo(), runtime=_FakeRuntime())
        invocation = invoker._build_invocation(
            _prepared(tmp_path, kodo_mode="test", task_branch="my-branch"),
        )
        assert invocation.metadata["kodo_mode"] == "test"
        assert invocation.metadata["task_branch"] == "my-branch"
        assert "orchestrator_override" not in invocation.metadata

    def test_orchestrator_override_lands_in_metadata(self, tmp_path):
        kodo = _mock_kodo()
        from types import SimpleNamespace
        kodo.settings.model_copy.return_value = SimpleNamespace(timeout_seconds=None)
        invoker = KodoBackendInvoker(kodo, runtime=_FakeRuntime())
        invocation = invoker._build_invocation(
            _prepared(tmp_path, orchestrator_override="claude-code:opus"),
        )
        assert invocation.metadata["orchestrator_override"] == "claude-code:opus"


class TestRuntimeDelegation:
    """The invoker hands the RuntimeInvocation to ExecutorRuntime and
    converts the returned RuntimeResult into a KodoRunCapture.
    """

    def test_runtime_receives_the_built_invocation(self, tmp_path):
        kodo = _mock_kodo()
        runtime = _FakeRuntime()
        invoker = KodoBackendInvoker(kodo, runtime=runtime)
        invoker.invoke(_prepared(tmp_path))
        assert runtime.last_invocation is not None
        assert runtime.last_invocation.runtime_name == "kodo"
        assert runtime.last_invocation.runtime_kind == "subprocess"

    def test_succeeded_status_propagates(self, tmp_path):
        runtime = _FakeRuntime(status="succeeded", exit_code=0)
        capture = KodoBackendInvoker(_mock_kodo(), runtime=runtime).invoke(_prepared(tmp_path))
        assert capture.exit_code == 0
        assert capture.timeout_hit is False

    def test_failed_status_propagates(self, tmp_path):
        runtime = _FakeRuntime(status="failed", exit_code=1, stderr="boom")
        capture = KodoBackendInvoker(_mock_kodo(), runtime=runtime).invoke(_prepared(tmp_path))
        assert capture.exit_code == 1
        assert capture.timeout_hit is False

    def test_timed_out_status_sets_timeout_hit(self, tmp_path):
        runtime = _FakeRuntime(status="timed_out", exit_code=-1)
        capture = KodoBackendInvoker(_mock_kodo(), runtime=runtime).invoke(_prepared(tmp_path))
        assert capture.timeout_hit is True


class TestRoundTrip:
    """Full ``invoke()`` end-to-end through the fake ExecutorRuntime."""

    def test_full_invocation_returns_kodo_capture(self, tmp_path):
        runtime = _FakeRuntime(stdout="hello", exit_code=0)
        capture = KodoBackendInvoker(_mock_kodo(), runtime=runtime).invoke(_prepared(tmp_path))
        assert capture.run_id == "run-rxp-1"
        assert capture.exit_code == 0
        assert capture.stdout == "hello"
        assert capture.timeout_hit is False
        assert capture.command == ["kodo", "--goal-file", "/tmp/g.md", "--project", "/repo"]
