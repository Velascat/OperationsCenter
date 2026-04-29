"""Tests for DirectLocalBackendAdapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch


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


def _adapter(**kw) -> DirectLocalBackendAdapter:
    return DirectLocalBackendAdapter(_settings(**kw))


def _fake_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Canonical result type
# ---------------------------------------------------------------------------


class TestCanonicalResult:
    def test_returns_execution_result_instance(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=0)):
            result = _adapter().execute(_request(tmp_path))
        assert isinstance(result, ExecutionResult)

    def test_result_is_json_serialisable(self, tmp_path):
        import json

        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=0)):
            result = _adapter().execute(_request(tmp_path))
        parsed = json.loads(result.model_dump_json())
        assert "status" in parsed
        assert "success" in parsed

    def test_proposal_id_preserved(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=0)):
            result = _adapter().execute(_request(tmp_path, proposal_id="prop-xyz"))
        assert result.proposal_id == "prop-xyz"

    def test_run_id_set(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=0)):
            result = _adapter().execute(_request(tmp_path))
        assert result.run_id


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class TestSuccess:
    def test_zero_exit_is_success(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=0)):
            result = _adapter().execute(_request(tmp_path))
        assert result.success is True
        assert result.status == ExecutionStatus.SUCCEEDED

    def test_success_has_no_failure_category(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=0)):
            result = _adapter().execute(_request(tmp_path))
        assert result.failure_category is None

    def test_stdout_captured_as_artifact(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=0, stdout="done")):
            result = _adapter().execute(_request(tmp_path))
        assert len(result.artifacts) >= 1


# ---------------------------------------------------------------------------
# Missing binary
# ---------------------------------------------------------------------------


class TestMissingBinary:
    def test_missing_binary_returns_failed_result(self, tmp_path):
        result = _adapter(binary="__nonexistent_binary__").execute(_request(tmp_path))
        assert result.success is False

    def test_missing_binary_sets_backend_error_category(self, tmp_path):
        result = _adapter(binary="__nonexistent_binary__").execute(_request(tmp_path))
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR

    def test_missing_binary_failure_reason_populated(self, tmp_path):
        result = _adapter(binary="__nonexistent_binary__").execute(_request(tmp_path))
        assert result.failure_reason is not None
        assert len(result.failure_reason) > 0

    def test_missing_binary_status_is_failed(self, tmp_path):
        result = _adapter(binary="__nonexistent_binary__").execute(_request(tmp_path))
        assert result.status == ExecutionStatus.FAILED


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_timeout_returns_failed_result(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="aider", timeout=30)):
            result = _adapter(timeout_seconds=30).execute(_request(tmp_path))
        assert result.success is False

    def test_timeout_sets_timeout_status(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="aider", timeout=30)):
            result = _adapter(timeout_seconds=30).execute(_request(tmp_path))
        assert result.status == ExecutionStatus.TIMED_OUT

    def test_timeout_sets_timeout_failure_category(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="aider", timeout=30)):
            result = _adapter(timeout_seconds=30).execute(_request(tmp_path))
        assert result.failure_category == FailureReasonCategory.TIMEOUT

    def test_timeout_failure_reason_mentions_timeout(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="aider", timeout=30)):
            result = _adapter(timeout_seconds=30).execute(_request(tmp_path))
        assert "imed out" in (result.failure_reason or "") or "imed out" in (
            result.artifacts[0].content if result.artifacts else ""
        )


# ---------------------------------------------------------------------------
# Non-zero exit
# ---------------------------------------------------------------------------


class TestNonZeroExit:
    def test_nonzero_exit_is_failure(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=1, stderr="something broke")):
            result = _adapter().execute(_request(tmp_path))
        assert result.success is False

    def test_nonzero_exit_sets_failed_status(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=1)):
            result = _adapter().execute(_request(tmp_path))
        assert result.status == ExecutionStatus.FAILED

    def test_nonzero_exit_sets_backend_error_category(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=2)):
            result = _adapter().execute(_request(tmp_path))
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR

    def test_nonzero_exit_failure_reason_populated(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=1, stderr="lint failure")):
            result = _adapter().execute(_request(tmp_path))
        assert result.failure_reason is not None

    def test_no_routing_logic_in_result(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run", return_value=_fake_proc(returncode=1)):
            result = _adapter().execute(_request(tmp_path))
        assert not hasattr(result, "selected_lane")
        assert not hasattr(result, "selected_backend")
        assert not hasattr(result, "policy_rule_matched")
