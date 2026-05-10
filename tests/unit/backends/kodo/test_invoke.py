# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for kodo invocation boundary (KodoBackendInvoker)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rxp.contracts import RuntimeInvocation, RuntimeResult

from operations_center.backends.kodo.invoke import KodoBackendInvoker, _extract_artifacts
from operations_center.backends.kodo.models import KodoPreparedRun
from operations_center.backends.kodo.runner import KodoAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prepared(tmp_path: Path, **kw) -> KodoPreparedRun:
    defaults = dict(
        run_id="run-1",
        goal_text="Fix lint errors",
        constraints_text=None,
        repo_path=tmp_path / "repo",
        task_branch="auto/lint-fix",
        goal_file_path=tmp_path / "repo" / ".kodo_goal.md",
        validation_commands=[],
        timeout_seconds=300,
    )
    defaults.update(kw)
    return KodoPreparedRun(**defaults)


def _mock_kodo() -> KodoAdapter:
    """Mock the kodo runner — only the methods the invoker uses
    (build_command + write_goal_file). The actual subprocess call is
    delegated to a fake ExecutorRuntime, so _run_subprocess isn't
    mocked here.
    """
    kodo = MagicMock()
    kodo.build_command.return_value = ["kodo", "--goal-file", ".kodo_goal.md"]
    kodo.write_goal_file = MagicMock()
    return kodo


class _FakeRuntime:
    """ExecutorRuntime stand-in that writes desired stdout/stderr to
    the invocation's artifact_directory and returns an RxP RuntimeResult.
    """

    def __init__(self, *, stdout: str = "done", stderr: str = "",
                 exit_code: int = 0, status: str = "succeeded") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.status = status
        self.last_invocation: RuntimeInvocation | None = None

    def run(self, invocation: RuntimeInvocation) -> RuntimeResult:
        self.last_invocation = invocation
        ar_dir = Path(invocation.artifact_directory) if invocation.artifact_directory else Path("/tmp")
        ar_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = ar_dir / "stdout.txt"
        stderr_path = ar_dir / "stderr.txt"
        stdout_path.write_text(self.stdout, encoding="utf-8")
        stderr_path.write_text(self.stderr, encoding="utf-8")
        now = datetime.now(timezone.utc).isoformat()
        return RuntimeResult(
            invocation_id=invocation.invocation_id,
            runtime_name=invocation.runtime_name,
            runtime_kind=invocation.runtime_kind,
            status=self.status,
            exit_code=self.exit_code,
            started_at=now,
            finished_at=now,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )


def _invoker(kodo: KodoAdapter, runtime: _FakeRuntime | None = None) -> KodoBackendInvoker:
    return KodoBackendInvoker(kodo, runtime=runtime or _FakeRuntime())


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------

class TestInvocation:
    def test_invoke_calls_kodo_run(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        kodo = _mock_kodo()
        runtime = _FakeRuntime()
        invoker = _invoker(kodo, runtime)
        invoker.invoke(_prepared(tmp_path))
        # The runtime is what actually executes the subprocess now.
        assert runtime.last_invocation is not None
        assert runtime.last_invocation.runtime_name == "kodo"

    def test_invoke_calls_write_goal_file(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        kodo = _mock_kodo()
        invoker = _invoker(kodo)
        invoker.invoke(_prepared(tmp_path))
        kodo.write_goal_file.assert_called_once()

    def test_capture_run_id_matches_prepared(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        capture = _invoker(_mock_kodo()).invoke(_prepared(tmp_path, run_id="my-run"))
        assert capture.run_id == "my-run"

    def test_capture_exit_code(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        runtime = _FakeRuntime(stdout="", stderr="error", exit_code=1, status="failed")
        capture = _invoker(_mock_kodo(), runtime).invoke(_prepared(tmp_path))
        assert capture.exit_code == 1
        assert capture.succeeded is False

    def test_capture_succeeded_on_exit_zero(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        capture = _invoker(_mock_kodo()).invoke(_prepared(tmp_path))
        assert capture.succeeded is True

    def test_capture_has_duration(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        capture = _invoker(_mock_kodo()).invoke(_prepared(tmp_path))
        assert capture.duration_ms >= 0

    def test_timeout_hit_flag_set(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        runtime = _FakeRuntime(exit_code=-1, status="timed_out")
        capture = _invoker(_mock_kodo(), runtime).invoke(_prepared(tmp_path))
        assert capture.timeout_hit is True

    def test_goal_file_deleted_after_run(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        goal_file = tmp_path / "repo" / ".kodo_goal.md"
        invoker = _invoker(_mock_kodo())
        invoker.invoke(_prepared(tmp_path))
        # invoker should have attempted to unlink the goal file
        assert not goal_file.exists()


def test_invoker_does_not_inject_openai_api_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    kodo = _mock_kodo()
    runtime = _FakeRuntime()
    invoker = _invoker(kodo, runtime)
    invoker.invoke(_prepared(tmp_path))
    assert "OPENAI_API_BASE" not in runtime.last_invocation.environment


# ---------------------------------------------------------------------------
# Artifact extraction
# ---------------------------------------------------------------------------

class TestArtifactExtraction:
    def test_log_excerpt_artifact_created(self):
        artifacts = _extract_artifacts("output line 1\noutput line 2", "")
        assert len(artifacts) == 1
        assert artifacts[0].artifact_type == "log_excerpt"
        assert "output line 1" in artifacts[0].content

    def test_empty_output_no_artifacts(self):
        artifacts = _extract_artifacts("", "")
        assert artifacts == []

    def test_stderr_included_in_artifact(self):
        artifacts = _extract_artifacts("", "error text")
        assert "error text" in artifacts[0].content

    def test_long_output_truncated(self):
        long = "x" * 10_000
        artifacts = _extract_artifacts(long, "")
        assert len(artifacts[0].content) < len(long)
        assert "truncated" in artifacts[0].content
