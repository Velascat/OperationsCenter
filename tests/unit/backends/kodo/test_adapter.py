# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for KodoBackendAdapter (end-to-end through the full pipeline)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock


from operations_center.adapters.kodo.adapter import KodoAdapter, KodoRunResult
from operations_center.backends.kodo.adapter import KodoBackendAdapter
from operations_center.backends.kodo.models import KodoRunCapture
from operations_center.contracts.execution import ExecutionRequest
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _mock_kodo(exit_code: int = 0, stdout: str = "done", stderr: str = "") -> KodoAdapter:
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = KodoRunResult(exit_code=exit_code, stdout=stdout, stderr=stderr, command=["kodo"])
    kodo.write_goal_file = MagicMock()
    KodoAdapter.is_orchestrator_rate_limited = staticmethod(lambda r: False)
    KodoAdapter.is_quota_exhausted = staticmethod(lambda r: False)
    return kodo


def _adapter(kodo: KodoAdapter = None) -> KodoBackendAdapter:
    if kodo is None:
        kodo = _mock_kodo()
    return KodoBackendAdapter(kodo)


# ---------------------------------------------------------------------------
# supports()
# ---------------------------------------------------------------------------

class TestSupports:
    def test_valid_request_is_supported(self, tmp_path):
        adapter = _adapter()
        check = adapter.supports(_request(tmp_path))
        assert check.supported is True

    def test_empty_goal_not_supported(self, tmp_path):
        adapter = _adapter()
        check = adapter.supports(_request(tmp_path, goal_text=""))
        assert check.supported is False

    def test_empty_repo_key_not_supported(self, tmp_path):
        adapter = _adapter()
        check = adapter.supports(_request(tmp_path, repo_key=""))
        assert check.supported is False


# ---------------------------------------------------------------------------
# execute() — success path
# ---------------------------------------------------------------------------

class TestExecuteSuccess:
    def test_returns_execution_result(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        adapter = _adapter(_mock_kodo(exit_code=0))
        result = adapter.execute(_request(tmp_path))
        from operations_center.contracts.execution import ExecutionResult
        assert isinstance(result, ExecutionResult)

    def test_success_result(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        adapter = _adapter(_mock_kodo(exit_code=0))
        result = adapter.execute(_request(tmp_path))
        assert result.success is True
        assert result.status == ExecutionStatus.SUCCEEDED

    def test_run_id_preserved(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        r = _request(tmp_path)
        result = _adapter(_mock_kodo()).execute(r)
        assert result.run_id == r.run_id

    def test_proposal_id_preserved(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        result = _adapter(_mock_kodo()).execute(_request(tmp_path))
        assert result.proposal_id == "prop-1"


class TestExecuteAndCapture:
    def test_returns_result_and_capture(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        result, capture = _adapter(_mock_kodo()).execute_and_capture(_request(tmp_path))
        assert result.run_id is not None
        assert isinstance(capture, KodoRunCapture)

    def test_unsupported_request_returns_none_capture(self, tmp_path):
        result, capture = _adapter(_mock_kodo()).execute_and_capture(_request(tmp_path, goal_text=""))
        assert result.failure_category == FailureReasonCategory.UNSUPPORTED_REQUEST
        assert capture is None

    def test_capture_does_not_leak_into_canonical_result(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        result, _ = _adapter(_mock_kodo()).execute_and_capture(_request(tmp_path))
        assert not hasattr(result, "stdout")
        assert not hasattr(result, "stderr")


class TestBackendDetailRefs:
    def test_build_backend_detail_refs_retains_raw_kodo_details_by_reference(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        req = _request(tmp_path)
        adapter = _adapter(_mock_kodo(stdout="hello", stderr="warn"))

        _, capture = adapter.execute_and_capture(req)

        refs = adapter.build_backend_detail_refs(req, capture)

        assert {ref.detail_type for ref in refs} == {"stdout_log", "stderr_log", "structured_result"}
        for ref in refs:
            assert ref.path is not None
            assert Path(ref.path).is_file()

        structured_ref = next(ref for ref in refs if ref.detail_type == "structured_result")
        payload = json.loads(Path(structured_ref.path).read_text(encoding="utf-8"))
        assert payload["run_id"] == req.run_id
        assert payload["duration_ms"] >= 0
        assert payload["command"] == ["kodo"]


# ---------------------------------------------------------------------------
# execute() — failure path
# ---------------------------------------------------------------------------

class TestExecuteFailure:
    def test_nonzero_exit_is_failure(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        adapter = _adapter(_mock_kodo(exit_code=1, stderr="something broke"))
        result = adapter.execute(_request(tmp_path))
        assert result.success is False
        assert result.status == ExecutionStatus.FAILED

    def test_failure_reason_populated(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        adapter = _adapter(_mock_kodo(exit_code=1, stderr="kodo error detail"))
        result = adapter.execute(_request(tmp_path))
        assert result.failure_reason is not None

    def test_failure_category_set(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        adapter = _adapter(_mock_kodo(exit_code=1))
        result = adapter.execute(_request(tmp_path))
        assert result.failure_category is not None


# ---------------------------------------------------------------------------
# execute() — unsupported request
# ---------------------------------------------------------------------------

class TestUnsupportedRequest:
    def test_unsupported_returns_unsupported_request(self, tmp_path):
        adapter = _adapter()
        result = adapter.execute(_request(tmp_path, goal_text=""))
        assert result.success is False
        assert result.failure_category == FailureReasonCategory.UNSUPPORTED_REQUEST

    def test_unsupported_does_not_invoke_kodo(self, tmp_path):
        kodo = _mock_kodo()
        adapter = _adapter(kodo)
        adapter.execute(_request(tmp_path, goal_text=""))
        kodo.run.assert_not_called()


# ---------------------------------------------------------------------------
# execute() — invocation error
# ---------------------------------------------------------------------------

class TestInvocationError:
    def test_kodo_crash_returns_backend_error(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        kodo = MagicMock(spec=KodoAdapter)
        kodo.run.side_effect = RuntimeError("kodo crashed unexpectedly")
        kodo.write_goal_file = MagicMock()
        KodoAdapter.is_orchestrator_rate_limited = staticmethod(lambda r: False)
        KodoAdapter.is_quota_exhausted = staticmethod(lambda r: False)
        adapter = _adapter(kodo)
        result = adapter.execute(_request(tmp_path))
        assert result.success is False
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
        assert "invocation failed" in result.failure_reason


# ---------------------------------------------------------------------------
# from_settings factory
# ---------------------------------------------------------------------------

class TestFromSettings:
    def test_factory_returns_adapter_instance(self):
        adapter = KodoBackendAdapter.from_settings()
        assert isinstance(adapter, KodoBackendAdapter)


# ---------------------------------------------------------------------------
# No kodo-native types escape the adapter
# ---------------------------------------------------------------------------

class TestBoundaryEnforcement:
    def test_result_is_canonical_execution_result(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        from operations_center.contracts.execution import ExecutionResult
        result = _adapter(_mock_kodo()).execute(_request(tmp_path))
        assert isinstance(result, ExecutionResult)

    def test_result_is_json_serialisable(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        import json
        result = _adapter(_mock_kodo()).execute(_request(tmp_path))
        parsed = json.loads(result.model_dump_json())
        assert "status" in parsed
        assert "success" in parsed
