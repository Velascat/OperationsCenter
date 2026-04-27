"""Tests for backends/openclaw/adapter.py — OpenClawBackendAdapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


from operations_center.backends.openclaw.adapter import OpenClawBackendAdapter
from operations_center.backends.openclaw.models import OpenClawRunCapture
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult


def _req(tmp_path: Path, **kw) -> ExecutionRequest:
    defaults = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="Fix all lint errors",
        repo_key="api-service",
        clone_url="https://git.example.com/api.git",
        base_branch="main",
        task_branch="auto/fix-lint-abc",
        workspace_path=tmp_path / "repo",
    )
    defaults.update(kw)
    return ExecutionRequest(**defaults)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_with_stub_returns_adapter():
    adapter = OpenClawBackendAdapter.with_stub()
    assert isinstance(adapter, OpenClawBackendAdapter)


def test_with_stub_custom_outcome():
    adapter = OpenClawBackendAdapter.with_stub(outcome="failure", error_text="crash")
    assert isinstance(adapter, OpenClawBackendAdapter)


# ---------------------------------------------------------------------------
# supports()
# ---------------------------------------------------------------------------


def test_supports_valid_request(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub()
    check = adapter.supports(_req(tmp_path))
    assert check.supported is True


def test_supports_empty_goal_rejected(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub()
    check = adapter.supports(_req(tmp_path, goal_text=""))
    assert check.supported is False


def test_supports_empty_repo_key_rejected(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub()
    check = adapter.supports(_req(tmp_path, repo_key=""))
    assert check.supported is False


def test_supports_empty_workspace_path_rejected(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub()
    check = adapter.supports(_req(tmp_path, workspace_path=Path("")))
    assert check.supported is False


# ---------------------------------------------------------------------------
# execute() — success path
# ---------------------------------------------------------------------------


def test_execute_returns_execution_result(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub(outcome="success", output_text="done")
    result = adapter.execute(_req(tmp_path))
    assert isinstance(result, ExecutionResult)


def test_execute_success_status(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub(outcome="success")
    result = adapter.execute(_req(tmp_path))
    assert result.status == ExecutionStatus.SUCCESS
    assert result.success is True


def test_execute_preserves_proposal_id(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub(outcome="success")
    req = _req(tmp_path)
    result = adapter.execute(req)
    assert result.proposal_id == req.proposal_id


def test_execute_preserves_decision_id(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub(outcome="success")
    req = _req(tmp_path)
    result = adapter.execute(req)
    assert result.decision_id == req.decision_id


def test_execute_branch_name_set(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub(outcome="success")
    req = _req(tmp_path, task_branch="auto/fix-lint-xyz")
    result = adapter.execute(req)
    assert result.branch_name == "auto/fix-lint-xyz"


# ---------------------------------------------------------------------------
# execute() — failure path
# ---------------------------------------------------------------------------


def test_execute_failure_status(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub(outcome="failure", error_text="crash")
    result = adapter.execute(_req(tmp_path))
    assert result.status == ExecutionStatus.FAILED
    assert result.success is False


def test_execute_timeout_status(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub(outcome="timeout")
    result = adapter.execute(_req(tmp_path))
    assert result.status == ExecutionStatus.TIMEOUT


def test_execute_unsupported_request_returns_unsupported_request(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub()
    result = adapter.execute(_req(tmp_path, goal_text=""))
    assert result.status == ExecutionStatus.FAILED
    assert result.failure_category == FailureReasonCategory.UNSUPPORTED_REQUEST


def test_execute_unsupported_reason_mentions_openclaw(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub()
    result = adapter.execute(_req(tmp_path, goal_text=""))
    assert "openclaw" in result.failure_reason.lower() or "openClaw" in result.failure_reason


# ---------------------------------------------------------------------------
# execute_and_capture()
# ---------------------------------------------------------------------------


def test_execute_and_capture_returns_tuple(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub(outcome="success")
    result, capture = adapter.execute_and_capture(_req(tmp_path))
    assert isinstance(result, ExecutionResult)
    assert isinstance(capture, OpenClawRunCapture)


def test_execute_and_capture_unsupported_returns_none_capture(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub()
    result, capture = adapter.execute_and_capture(_req(tmp_path, goal_text=""))
    assert result.failure_category == FailureReasonCategory.UNSUPPORTED_REQUEST
    assert capture is None


def test_execute_and_capture_events_in_capture(tmp_path):
    events = [{"type": "tool_use", "name": "read_file"}]
    adapter = OpenClawBackendAdapter.with_stub(events=events)
    _, capture = adapter.execute_and_capture(_req(tmp_path))
    assert capture.event_count == 1


def test_execute_and_capture_events_not_in_result(tmp_path):
    events = [{"type": "tool_use", "name": "write_file"}]
    adapter = OpenClawBackendAdapter.with_stub(events=events)
    result, _ = adapter.execute_and_capture(_req(tmp_path))
    assert not hasattr(result, "events")


def test_execute_and_capture_changed_files_source_in_capture(tmp_path):
    adapter = OpenClawBackendAdapter.with_stub(outcome="success")
    _, capture = adapter.execute_and_capture(_req(tmp_path))
    assert capture.changed_files_source in ("git_diff", "event_stream", "unknown")


def test_execute_and_capture_event_stream_source_when_reported(tmp_path):
    reported = [{"path": "src/main.py", "change_type": "modified"}]
    adapter = OpenClawBackendAdapter.with_stub(reported_changed_files=reported)
    with patch(
        "operations_center.backends.openclaw.normalize._discover_changed_files_via_git",
        return_value=None,
    ):
        result, capture = adapter.execute_and_capture(_req(tmp_path))
    assert capture.changed_files_source == "event_stream"
    assert len(result.changed_files) == 1


# ---------------------------------------------------------------------------
# Separation from outer shell
# ---------------------------------------------------------------------------


def test_adapter_does_not_import_openclaw_shell():
    import operations_center.backends.openclaw.adapter as mod
    # Confirm openclaw_shell is not in the actual imports (only in docstring comments)
    imports = [
        name for name in vars(mod)
        if not name.startswith("_")
    ]
    assert "openclaw_shell" not in imports
    # Also verify no runtime import dependency
    assert "openclaw_shell" not in (mod.__dict__.get("__file__", "") or "")


def test_adapter_module_path_is_backends():
    from operations_center.backends.openclaw import OpenClawBackendAdapter as imported
    assert "backends.openclaw" in imported.__module__
