"""Tests for openclaw_shell/service.py — OpenClawShellService."""

from __future__ import annotations

import pytest

from operations_center.contracts.enums import ExecutionStatus, ValidationStatus
from operations_center.openclaw_shell.models import (
    OperatorContext,
    ShellRunHandle,
    ShellStatusSummary,
    ShellInspectionResult,
)
from operations_center.openclaw_shell.service import OpenClawShellService

from ..observability.conftest import (
    make_artifact,
    make_changed_file,
    make_result,
)
from operations_center.contracts.enums import ArtifactType, FailureReasonCategory
from operations_center.observability.recorder import ExecutionRecorder
from operations_center.observability.trace import RunReportBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(**kw) -> OperatorContext:
    defaults = dict(
        goal_text="Fix all lint errors",
        repo_key="svc",
        clone_url="https://github.com/example/svc.git",
    )
    defaults.update(kw)
    return OperatorContext(**defaults)


def _success_result(**kw):
    return make_result(
        run_id="run-svc01",
        proposal_id="prop-svc01",
        decision_id="dec-svc01",
        status=ExecutionStatus.SUCCESS,
        success=True,
        changed_files=[make_changed_file("src/main.py")],
        validation_status=ValidationStatus.PASSED,
        validation_commands_run=1,
        validation_commands_passed=1,
        **kw,
    )


def _failed_result(**kw):
    return make_result(
        run_id="run-svcf01",
        proposal_id="prop-svcf01",
        decision_id="dec-svcf01",
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason="kodo exited 1",
        **kw,
    )


@pytest.fixture
def stub_svc() -> OpenClawShellService:
    return OpenClawShellService.with_stub_routing(
        lane="claude_cli", backend="kodo", confidence=0.9
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_with_stub_routing_returns_service():
    svc = OpenClawShellService.with_stub_routing()
    assert isinstance(svc, OpenClawShellService)


def test_default_returns_service():
    svc = OpenClawShellService.default()
    assert isinstance(svc, OpenClawShellService)


# ---------------------------------------------------------------------------
# plan()
# ---------------------------------------------------------------------------


def test_plan_returns_handle(stub_svc):
    ctx = _ctx()
    handle = stub_svc.plan(ctx)
    assert isinstance(handle, ShellRunHandle)


def test_plan_handle_has_ids(stub_svc):
    ctx = _ctx()
    handle = stub_svc.plan(ctx)
    assert handle.proposal_id
    assert handle.decision_id
    assert handle.handle_id


def test_plan_handle_lane_backend(stub_svc):
    ctx = _ctx()
    handle = stub_svc.plan(ctx)
    assert handle.selected_lane == "claude_cli"
    assert handle.selected_backend == "kodo"


def test_plan_handle_status_planned(stub_svc):
    ctx = _ctx()
    handle = stub_svc.plan(ctx)
    assert handle.status == "planned"


def test_plan_handle_confidence(stub_svc):
    ctx = _ctx()
    handle = stub_svc.plan(ctx)
    assert handle.routing_confidence == pytest.approx(0.9)


def test_plan_handle_is_frozen(stub_svc):
    ctx = _ctx()
    handle = stub_svc.plan(ctx)
    with pytest.raises(Exception):
        handle.status = "running"


def test_plan_unique_handles_per_call(stub_svc):
    ctx = _ctx()
    h1 = stub_svc.plan(ctx)
    h2 = stub_svc.plan(ctx)
    assert h1.handle_id != h2.handle_id


# ---------------------------------------------------------------------------
# plan_with_summary()
# ---------------------------------------------------------------------------


def test_plan_with_summary_returns_tuple(stub_svc):
    ctx = _ctx()
    result = stub_svc.plan_with_summary(ctx)
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_plan_with_summary_types(stub_svc):
    ctx = _ctx()
    handle, summary = stub_svc.plan_with_summary(ctx)
    assert isinstance(handle, ShellRunHandle)
    assert isinstance(summary, ShellStatusSummary)


def test_plan_with_summary_handle_matches_summary_ids(stub_svc):
    ctx = _ctx()
    handle, summary = stub_svc.plan_with_summary(ctx)
    assert handle.proposal_id == summary.proposal_id


def test_plan_with_summary_status_pending(stub_svc):
    ctx = _ctx()
    _handle, summary = stub_svc.plan_with_summary(ctx)
    assert summary.success is False


def test_plan_with_summary_lane_backend(stub_svc):
    ctx = _ctx()
    _handle, summary = stub_svc.plan_with_summary(ctx)
    assert summary.selected_lane == "claude_cli"
    assert summary.selected_backend == "kodo"


# ---------------------------------------------------------------------------
# summarize_result()
# ---------------------------------------------------------------------------


def test_summarize_result_returns_summary(stub_svc):
    result = _success_result()
    summary = stub_svc.summarize_result(result, lane="claude_cli", backend="kodo")
    assert isinstance(summary, ShellStatusSummary)


def test_summarize_result_success(stub_svc):
    result = _success_result()
    summary = stub_svc.summarize_result(result, lane="claude_cli", backend="kodo")
    assert summary.success is True
    assert summary.status == ExecutionStatus.SUCCESS.value


def test_summarize_result_failure(stub_svc):
    result = _failed_result()
    summary = stub_svc.summarize_result(result, lane="claude_cli", backend="kodo")
    assert summary.success is False


def test_summarize_result_lane_backend_preserved(stub_svc):
    result = _success_result()
    summary = stub_svc.summarize_result(result, lane="aider_local", backend="kodo")
    assert summary.selected_lane == "aider_local"
    assert summary.selected_backend == "kodo"


def test_summarize_result_recorded_at_set(stub_svc):
    result = _success_result()
    summary = stub_svc.summarize_result(result, lane="claude_cli", backend="kodo")
    assert summary.recorded_at is not None


def test_summarize_result_headline_nonempty(stub_svc):
    result = _success_result()
    summary = stub_svc.summarize_result(result, lane="claude_cli", backend="kodo")
    assert len(summary.headline) > 0


# ---------------------------------------------------------------------------
# summarize_result_lightweight()
# ---------------------------------------------------------------------------


def test_summarize_lightweight_returns_summary(stub_svc):
    result = _success_result()
    summary = stub_svc.summarize_result_lightweight(result)
    assert isinstance(summary, ShellStatusSummary)


def test_summarize_lightweight_success(stub_svc):
    result = _success_result()
    summary = stub_svc.summarize_result_lightweight(result)
    assert summary.success is True


def test_summarize_lightweight_no_recorded_at(stub_svc):
    result = _success_result()
    summary = stub_svc.summarize_result_lightweight(result)
    assert summary.recorded_at is None


def test_summarize_lightweight_lane_backend(stub_svc):
    result = _success_result()
    summary = stub_svc.summarize_result_lightweight(
        result, lane="claude_cli", backend="kodo"
    )
    assert summary.selected_lane == "claude_cli"
    assert summary.selected_backend == "kodo"


def test_summarize_lightweight_no_lane_backend(stub_svc):
    result = _success_result()
    summary = stub_svc.summarize_result_lightweight(result)
    assert summary.selected_lane is None
    assert summary.selected_backend is None


# ---------------------------------------------------------------------------
# inspect_record()
# ---------------------------------------------------------------------------


def test_inspect_record_returns_inspection(stub_svc):
    recorder = ExecutionRecorder()
    builder = RunReportBuilder()
    result = _success_result()
    record = recorder.record(result, backend="kodo", lane="claude_cli")
    trace = builder.build_report(record)
    r = stub_svc.inspect_record(record, trace)
    assert isinstance(r, ShellInspectionResult)


def test_inspect_record_status(stub_svc):
    recorder = ExecutionRecorder()
    builder = RunReportBuilder()
    result = _success_result()
    record = recorder.record(result, backend="kodo", lane="claude_cli")
    trace = builder.build_report(record)
    r = stub_svc.inspect_record(record, trace)
    assert r.status == ExecutionStatus.SUCCESS.value
    assert r.run_id == "run-svc01"


def test_inspect_record_trace_and_record_ids(stub_svc):
    recorder = ExecutionRecorder()
    builder = RunReportBuilder()
    result = _success_result()
    record = recorder.record(result, backend="kodo", lane="claude_cli")
    trace = builder.build_report(record)
    r = stub_svc.inspect_record(record, trace)
    assert r.trace_id == trace.trace_id
    assert r.record_id == record.record_id
