"""Tests for backends/archon/adapter.py — ArchonBackendAdapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


from operations_center.backends.archon.adapter import ArchonBackendAdapter
from operations_center.backends.archon.invoke import ArchonAdapter, ArchonRunResult
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.contracts.execution import ExecutionRequest


def _req(tmp_path: Path, **kw) -> ExecutionRequest:
    defaults = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="Refactor the login module",
        repo_key="auth-service",
        clone_url="https://git.example.com/auth.git",
        base_branch="main",
        task_branch="auto/refactor-login-abc",
        workspace_path=tmp_path / "repo",
    )
    defaults.update(kw)
    return ExecutionRequest(**defaults)


def _mock_archon(
    outcome: str = "success",
    output_text: str = "archon: done",
    error_text: str = "",
    workflow_events: list[dict] | None = None,
) -> ArchonAdapter:
    adapter = MagicMock(spec=ArchonAdapter)
    adapter.run.return_value = ArchonRunResult(
        outcome=outcome,
        exit_code=0 if outcome == "success" else 1,
        output_text=output_text,
        error_text=error_text,
        workflow_events=workflow_events or [],
    )
    return adapter


def _adapter(archon=None) -> ArchonBackendAdapter:
    return ArchonBackendAdapter(archon or _mock_archon())


# ---------------------------------------------------------------------------
# supports()
# ---------------------------------------------------------------------------


def test_valid_request_is_supported(tmp_path):
    check = _adapter().supports(_req(tmp_path))
    assert check.supported is True


def test_empty_goal_not_supported(tmp_path):
    check = _adapter().supports(_req(tmp_path, goal_text=""))
    assert check.supported is False


def test_missing_repo_key_not_supported(tmp_path):
    check = _adapter().supports(_req(tmp_path, repo_key=""))
    assert check.supported is False


# ---------------------------------------------------------------------------
# execute() — happy path
# ---------------------------------------------------------------------------


def test_execute_returns_execution_result(tmp_path):
    from operations_center.contracts.execution import ExecutionResult
    result = _adapter().execute(_req(tmp_path))
    assert isinstance(result, ExecutionResult)


def test_successful_execution_gives_success_status(tmp_path):
    result = _adapter(_mock_archon(outcome="success")).execute(_req(tmp_path))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.success is True


def test_result_has_run_id(tmp_path):
    req = _req(tmp_path)
    result = _adapter().execute(req)
    assert result.run_id == req.run_id


def test_result_has_proposal_id(tmp_path):
    req = _req(tmp_path)
    result = _adapter().execute(req)
    assert result.proposal_id == req.proposal_id


# ---------------------------------------------------------------------------
# execute() — failure paths
# ---------------------------------------------------------------------------


def test_unsupported_request_returns_unsupported_request(tmp_path):
    result = _adapter().execute(_req(tmp_path, goal_text=""))
    assert result.status == ExecutionStatus.FAILED
    assert result.failure_category == FailureReasonCategory.UNSUPPORTED_REQUEST


def test_backend_failure_returns_failed_status(tmp_path):
    archon = _mock_archon(outcome="failure", error_text="workflow step failed")
    result = ArchonBackendAdapter(archon).execute(_req(tmp_path))
    assert result.status == ExecutionStatus.FAILED
    assert result.success is False


def test_timeout_outcome_gives_timeout_status(tmp_path):
    archon = _mock_archon(outcome="timeout")
    result = ArchonBackendAdapter(archon).execute(_req(tmp_path))
    assert result.status == ExecutionStatus.TIMEOUT
    assert result.failure_category == FailureReasonCategory.TIMEOUT


def test_invocation_exception_returns_backend_error(tmp_path):
    archon = MagicMock(spec=ArchonAdapter)
    archon.run.side_effect = RuntimeError("connection refused")
    result = ArchonBackendAdapter(archon).execute(_req(tmp_path))
    assert result.status == ExecutionStatus.FAILED
    assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert "invocation failed" in (result.failure_reason or "").lower()


# ---------------------------------------------------------------------------
# execute_and_capture()
# ---------------------------------------------------------------------------


def test_execute_and_capture_returns_tuple(tmp_path):
    result, capture = _adapter().execute_and_capture(_req(tmp_path))
    assert result is not None
    assert capture is not None


def test_execute_and_capture_capture_has_workflow_events(tmp_path):
    events = [{"step": "plan", "ok": True}]
    archon = _mock_archon(workflow_events=events)
    _, capture = ArchonBackendAdapter(archon).execute_and_capture(_req(tmp_path))
    assert capture is not None
    assert len(capture.workflow_events) == 1


def test_execute_and_capture_returns_none_capture_when_unsupported(tmp_path):
    result, capture = _adapter().execute_and_capture(_req(tmp_path, goal_text=""))
    assert result.failure_category == FailureReasonCategory.UNSUPPORTED_REQUEST
    assert capture is None


def test_execute_and_capture_returns_none_capture_on_mapping_error(tmp_path):
    archon = _mock_archon()
    adapter = ArchonBackendAdapter(archon)
    # Force a mapping error by providing an unsupported request
    result, capture = adapter.execute_and_capture(_req(tmp_path, goal_text=""))
    assert capture is None


def test_workflow_events_not_in_canonical_result(tmp_path):
    events = [{"step": "execute", "ok": True}]
    archon = _mock_archon(workflow_events=events)
    result, capture = ArchonBackendAdapter(archon).execute_and_capture(_req(tmp_path))
    # Events are accessible via capture but not present in canonical result
    assert not hasattr(result, "workflow_events")
    assert capture is not None
    assert len(capture.workflow_events) == 1


# ---------------------------------------------------------------------------
# with_stub() factory
# ---------------------------------------------------------------------------


def test_with_stub_creates_adapter(tmp_path):
    adapter = ArchonBackendAdapter.with_stub(outcome="success", output_text="done")
    result = adapter.execute(_req(tmp_path))
    assert result.success is True


def test_with_stub_failure_creates_adapter(tmp_path):
    adapter = ArchonBackendAdapter.with_stub(outcome="failure", error_text="failed")
    result = adapter.execute(_req(tmp_path))
    assert result.success is False


# ---------------------------------------------------------------------------
# Workflow type propagation
# ---------------------------------------------------------------------------


def test_workflow_type_default_is_goal(tmp_path):
    archon = _mock_archon()
    adapter = ArchonBackendAdapter(archon, workflow_type="goal")
    adapter.execute(_req(tmp_path))
    call_args = archon.run.call_args[0][0]
    assert call_args.workflow_type == "goal"


def test_custom_workflow_type_propagated(tmp_path):
    archon = _mock_archon()
    adapter = ArchonBackendAdapter(archon, workflow_type="fix_pr")
    adapter.execute(_req(tmp_path))
    call_args = archon.run.call_args[0][0]
    assert call_args.workflow_type == "fix_pr"
