# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for DirectLocalBackendAdapter.

Phase 3 — the adapter delegates subprocess execution to ExecutorRuntime,
so tests inject a fake runtime via the new ``runtime=`` parameter
rather than patching ``subprocess.run`` globally.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rxp.contracts import RuntimeInvocation, RuntimeResult

from operations_center.backends.direct_local.adapter import DirectLocalBackendAdapter
from operations_center.config.settings import AiderSettings
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**kw) -> AiderSettings:
    defaults = dict(binary="aider", model_prefix="openai", profile="capable", timeout_seconds=30)
    defaults.update(kw)
    return AiderSettings(**defaults)


def _request(tmp_path: Path, **kw) -> ExecutionRequest:
    defaults = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="Fix all lint errors",
        repo_key="api-service",
        clone_url="https://git.example.com/api.git",
        base_branch="main",
        task_branch="auto/lint-abc",
        workspace_path=tmp_path / "repo",
    )
    defaults.update(kw)
    return ExecutionRequest(**defaults)


class _FakeRuntime:
    """ExecutorRuntime stand-in that writes the configured stdout/stderr
    to the invocation's artifact_directory and returns a synthetic
    RuntimeResult. The exit_code is auto-derived from status when not
    explicitly set.
    """
    def __init__(self, *, status: str = "succeeded", stdout: str = "",
                 stderr: str = "", exit_code: int | None = None,
                 raise_exc: BaseException | None = None) -> None:
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code if exit_code is not None else (
            0 if status == "succeeded" else 1
        )
        self.raise_exc = raise_exc

    def run(self, invocation: RuntimeInvocation) -> RuntimeResult:
        if self.raise_exc is not None:
            raise self.raise_exc
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


def _adapter(runtime: _FakeRuntime | None = None, **settings_kw) -> DirectLocalBackendAdapter:
    return DirectLocalBackendAdapter(_settings(**settings_kw), runtime=runtime or _FakeRuntime())


# ---------------------------------------------------------------------------
# Canonical result type
# ---------------------------------------------------------------------------


class TestCanonicalResult:
    def test_returns_execution_result_instance(self, tmp_path):
        (tmp_path / "repo").mkdir()
        result = _adapter().execute(_request(tmp_path))
        assert isinstance(result, ExecutionResult)

    def test_result_is_json_serialisable(self, tmp_path):
        import json
        (tmp_path / "repo").mkdir()
        result = _adapter().execute(_request(tmp_path))
        parsed = json.loads(result.model_dump_json())
        assert "status" in parsed
        assert "success" in parsed

    def test_proposal_id_preserved(self, tmp_path):
        (tmp_path / "repo").mkdir()
        result = _adapter().execute(_request(tmp_path, proposal_id="prop-xyz"))
        assert result.proposal_id == "prop-xyz"

    def test_run_id_set(self, tmp_path):
        (tmp_path / "repo").mkdir()
        result = _adapter().execute(_request(tmp_path))
        assert result.run_id


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class TestSuccess:
    def test_zero_exit_is_success(self, tmp_path):
        (tmp_path / "repo").mkdir()
        result = _adapter().execute(_request(tmp_path))
        assert result.success is True
        assert result.status == ExecutionStatus.SUCCEEDED

    def test_success_has_no_failure_category(self, tmp_path):
        (tmp_path / "repo").mkdir()
        result = _adapter().execute(_request(tmp_path))
        assert result.failure_category is None

    def test_stdout_captured_as_artifact(self, tmp_path):
        (tmp_path / "repo").mkdir()
        result = _adapter(_FakeRuntime(stdout="done")).execute(_request(tmp_path))
        assert len(result.artifacts) >= 1


# ---------------------------------------------------------------------------
# Missing binary
# ---------------------------------------------------------------------------


class TestMissingBinary:
    def test_missing_binary_returns_failed_result(self, tmp_path):
        runtime = _FakeRuntime(raise_exc=FileNotFoundError("aider"))
        result = _adapter(runtime, binary="__nonexistent_binary__").execute(_request(tmp_path))
        assert result.success is False

    def test_missing_binary_sets_backend_error_category(self, tmp_path):
        runtime = _FakeRuntime(raise_exc=FileNotFoundError("aider"))
        result = _adapter(runtime, binary="__nonexistent_binary__").execute(_request(tmp_path))
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR

    def test_missing_binary_failure_reason_populated(self, tmp_path):
        runtime = _FakeRuntime(raise_exc=FileNotFoundError("aider"))
        result = _adapter(runtime, binary="__nonexistent_binary__").execute(_request(tmp_path))
        assert result.failure_reason is not None
        assert len(result.failure_reason) > 0

    def test_missing_binary_status_is_failed(self, tmp_path):
        runtime = _FakeRuntime(raise_exc=FileNotFoundError("aider"))
        result = _adapter(runtime, binary="__nonexistent_binary__").execute(_request(tmp_path))
        assert result.status == ExecutionStatus.FAILED


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_timeout_returns_failed_result(self, tmp_path):
        (tmp_path / "repo").mkdir()
        result = _adapter(_FakeRuntime(status="timed_out"),
                          timeout_seconds=30).execute(_request(tmp_path))
        assert result.success is False

    def test_timeout_sets_timeout_status(self, tmp_path):
        (tmp_path / "repo").mkdir()
        result = _adapter(_FakeRuntime(status="timed_out"),
                          timeout_seconds=30).execute(_request(tmp_path))
        assert result.status == ExecutionStatus.TIMED_OUT

    def test_timeout_sets_timeout_failure_category(self, tmp_path):
        (tmp_path / "repo").mkdir()
        result = _adapter(_FakeRuntime(status="timed_out"),
                          timeout_seconds=30).execute(_request(tmp_path))
        assert result.failure_category == FailureReasonCategory.TIMEOUT

    def test_timeout_failure_reason_mentions_timeout(self, tmp_path):
        (tmp_path / "repo").mkdir()
        result = _adapter(_FakeRuntime(status="timed_out"),
                          timeout_seconds=30).execute(_request(tmp_path))
        assert "imed out" in (result.failure_reason or "") or "imed out" in (
            result.artifacts[0].content if result.artifacts else ""
        )


# ---------------------------------------------------------------------------
# Non-zero exit
# ---------------------------------------------------------------------------


class TestNonZeroExit:
    def test_nonzero_exit_is_failure(self, tmp_path):
        (tmp_path / "repo").mkdir()
        runtime = _FakeRuntime(status="failed", exit_code=1, stderr="something broke")
        result = _adapter(runtime).execute(_request(tmp_path))
        assert result.success is False

    def test_nonzero_exit_sets_failed_status(self, tmp_path):
        (tmp_path / "repo").mkdir()
        runtime = _FakeRuntime(status="failed", exit_code=1)
        result = _adapter(runtime).execute(_request(tmp_path))
        assert result.status == ExecutionStatus.FAILED

    def test_nonzero_exit_sets_backend_error_category(self, tmp_path):
        (tmp_path / "repo").mkdir()
        runtime = _FakeRuntime(status="failed", exit_code=2)
        result = _adapter(runtime).execute(_request(tmp_path))
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR

    def test_nonzero_exit_failure_reason_populated(self, tmp_path):
        (tmp_path / "repo").mkdir()
        runtime = _FakeRuntime(status="failed", exit_code=1, stderr="lint failure")
        result = _adapter(runtime).execute(_request(tmp_path))
        assert result.failure_reason is not None

    def test_no_routing_logic_in_result(self, tmp_path):
        (tmp_path / "repo").mkdir()
        runtime = _FakeRuntime(status="failed", exit_code=1)
        result = _adapter(runtime).execute(_request(tmp_path))
        assert not hasattr(result, "selected_lane")
        assert not hasattr(result, "selected_backend")
        assert not hasattr(result, "policy_rule_matched")
