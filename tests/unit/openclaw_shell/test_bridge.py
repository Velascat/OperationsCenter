"""Tests for openclaw_shell/bridge.py — OpenClawBridge."""

from __future__ import annotations


import pytest

from operations_center.contracts.enums import ExecutionStatus, ValidationStatus
from operations_center.openclaw_shell.bridge import OpenClawBridge
from operations_center.openclaw_shell.models import (
    OperatorContext,
    ShellActionResult,
    ShellInspectionResult,
    ShellRunHandle,
    ShellStatusSummary,
)

from ..observability.conftest import (
    make_changed_file,
    make_result,
)
from operations_center.contracts.enums import FailureReasonCategory
from operations_center.observability.recorder import ExecutionRecorder
from operations_center.observability.trace import RunReportBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(**kw) -> OperatorContext:
    defaults = dict(
        goal_text="Fix lint errors",
        repo_key="svc",
        clone_url="https://github.com/example/svc.git",
    )
    defaults.update(kw)
    return OperatorContext(**defaults)


def _success_result(**kw):
    return make_result(
        run_id="run-br01",
        proposal_id="prop-br01",
        decision_id="dec-br01",
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        changed_files=[make_changed_file("src/main.py")],
        validation_status=ValidationStatus.PASSED,
        validation_commands_run=1,
        validation_commands_passed=1,
        **kw,
    )


def _failed_result(**kw):
    return make_result(
        run_id="run-brf01",
        proposal_id="prop-brf01",
        decision_id="dec-brf01",
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason="kodo exited 1",
        **kw,
    )


@pytest.fixture
def bridge() -> OpenClawBridge:
    return OpenClawBridge.with_stub_routing(
        lane="claude_cli", backend="kodo", confidence=0.9
    )


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def test_with_stub_routing_returns_bridge():
    b = OpenClawBridge.with_stub_routing()
    assert isinstance(b, OpenClawBridge)


def test_default_returns_bridge():
    b = OpenClawBridge.default()
    assert isinstance(b, OpenClawBridge)


# ---------------------------------------------------------------------------
# is_enabled — optionality
# ---------------------------------------------------------------------------


def test_is_enabled_false_by_default(monkeypatch):
    monkeypatch.delenv("OPENCLAW_SHELL_ENABLED", raising=False)
    assert OpenClawBridge.is_enabled() is False


def test_is_enabled_true_when_set(monkeypatch):
    monkeypatch.setenv("OPENCLAW_SHELL_ENABLED", "1")
    assert OpenClawBridge.is_enabled() is True


def test_is_enabled_false_for_zero(monkeypatch):
    monkeypatch.setenv("OPENCLAW_SHELL_ENABLED", "0")
    assert OpenClawBridge.is_enabled() is False


def test_is_enabled_false_for_empty_string(monkeypatch):
    monkeypatch.setenv("OPENCLAW_SHELL_ENABLED", "")
    assert OpenClawBridge.is_enabled() is False


def test_is_enabled_false_for_wrong_value(monkeypatch):
    monkeypatch.setenv("OPENCLAW_SHELL_ENABLED", "true")
    assert OpenClawBridge.is_enabled() is False


def test_is_enabled_trims_whitespace(monkeypatch):
    monkeypatch.setenv("OPENCLAW_SHELL_ENABLED", "1 ")
    assert OpenClawBridge.is_enabled() is True


# ---------------------------------------------------------------------------
# trigger()
# ---------------------------------------------------------------------------


def test_trigger_returns_handle(bridge):
    ctx = _ctx()
    handle = bridge.trigger(ctx)
    assert isinstance(handle, ShellRunHandle)


def test_trigger_handle_lane_backend(bridge):
    ctx = _ctx()
    handle = bridge.trigger(ctx)
    assert handle.selected_lane == "claude_cli"
    assert handle.selected_backend == "kodo"


def test_trigger_handle_status_planned(bridge):
    ctx = _ctx()
    handle = bridge.trigger(ctx)
    assert handle.status == "planned"


def test_trigger_handle_is_frozen(bridge):
    ctx = _ctx()
    handle = bridge.trigger(ctx)
    with pytest.raises(Exception):
        handle.status = "running"


def test_trigger_unique_handles(bridge):
    ctx = _ctx()
    h1 = bridge.trigger(ctx)
    h2 = bridge.trigger(ctx)
    assert h1.handle_id != h2.handle_id


# ---------------------------------------------------------------------------
# trigger_with_summary()
# ---------------------------------------------------------------------------


def test_trigger_with_summary_returns_tuple(bridge):
    ctx = _ctx()
    result = bridge.trigger_with_summary(ctx)
    assert len(result) == 2


def test_trigger_with_summary_types(bridge):
    ctx = _ctx()
    handle, summary = bridge.trigger_with_summary(ctx)
    assert isinstance(handle, ShellRunHandle)
    assert isinstance(summary, ShellStatusSummary)


def test_trigger_with_summary_matching_ids(bridge):
    ctx = _ctx()
    handle, summary = bridge.trigger_with_summary(ctx)
    assert handle.proposal_id == summary.proposal_id


# ---------------------------------------------------------------------------
# status_from_result()
# ---------------------------------------------------------------------------


def test_status_from_result_success(bridge):
    result = _success_result()
    summary = bridge.status_from_result(result, lane="claude_cli", backend="kodo")
    assert isinstance(summary, ShellStatusSummary)
    assert summary.success is True


def test_status_from_result_failure(bridge):
    result = _failed_result()
    summary = bridge.status_from_result(result, lane="claude_cli", backend="kodo")
    assert summary.success is False


def test_status_from_result_lane_backend(bridge):
    result = _success_result()
    summary = bridge.status_from_result(result, lane="aider_local", backend="kodo")
    assert summary.selected_lane == "aider_local"
    assert summary.selected_backend == "kodo"


# ---------------------------------------------------------------------------
# status_from_result_lightweight()
# ---------------------------------------------------------------------------


def test_status_from_result_lightweight_returns_summary(bridge):
    result = _success_result()
    summary = bridge.status_from_result_lightweight(result)
    assert isinstance(summary, ShellStatusSummary)


def test_status_from_result_lightweight_no_recorded_at(bridge):
    result = _success_result()
    summary = bridge.status_from_result_lightweight(result)
    assert summary.recorded_at is None


# ---------------------------------------------------------------------------
# inspect_from_record()
# ---------------------------------------------------------------------------


def test_inspect_from_record_returns_inspection(bridge):
    recorder = ExecutionRecorder()
    builder = RunReportBuilder()
    result = _success_result()
    record = recorder.record(result, backend="kodo", lane="claude_cli")
    trace = builder.build_report(record)
    r = bridge.inspect_from_record(record, trace)
    assert isinstance(r, ShellInspectionResult)


def test_inspect_from_record_status(bridge):
    recorder = ExecutionRecorder()
    builder = RunReportBuilder()
    result = _success_result()
    record = recorder.record(result, backend="kodo", lane="claude_cli")
    trace = builder.build_report(record)
    r = bridge.inspect_from_record(record, trace)
    assert r.status == ExecutionStatus.SUCCEEDED.value


# ---------------------------------------------------------------------------
# wrap_action()
# ---------------------------------------------------------------------------


def test_wrap_action_success(bridge):
    def do_thing():
        pass

    result = bridge.wrap_action("test_action", do_thing)
    assert isinstance(result, ShellActionResult)
    assert result.success is True
    assert result.action == "test_action"
    assert result.message == "ok"


def test_wrap_action_exception_caught(bridge):
    def fail():
        raise ValueError("something went wrong")

    result = bridge.wrap_action("failing_action", fail)
    assert result.success is False
    assert result.action == "failing_action"
    assert "something went wrong" in result.message


def test_wrap_action_detail_is_exception_type(bridge):
    def fail():
        raise TypeError("type mismatch")

    result = bridge.wrap_action("type_action", fail)
    assert result.detail == "TypeError"


def test_wrap_action_with_args(bridge):
    collected = []

    def collect(a, b):
        collected.append((a, b))

    result = bridge.wrap_action("collect", collect, "x", "y")
    assert result.success is True
    assert collected == [("x", "y")]


def test_wrap_action_with_kwargs(bridge):
    collected = {}

    def store(**kw):
        collected.update(kw)

    result = bridge.wrap_action("store", store, key="val")
    assert result.success is True
    assert collected == {"key": "val"}


def test_wrap_action_result_is_frozen(bridge):
    def do_nothing():
        pass

    result = bridge.wrap_action("noop", do_nothing)
    with pytest.raises(Exception):
        result.success = False


def test_wrap_action_does_not_reraise(bridge):
    def always_raises():
        raise RuntimeError("fatal")

    result = bridge.wrap_action("boom", always_raises)
    assert result.success is False


# ---------------------------------------------------------------------------
# Shell does not invalidate internal architecture
# ---------------------------------------------------------------------------


def test_bridge_is_independent_of_enabled_flag(monkeypatch, bridge):
    """Bridge works regardless of OPENCLAW_SHELL_ENABLED."""
    monkeypatch.delenv("OPENCLAW_SHELL_ENABLED", raising=False)
    assert OpenClawBridge.is_enabled() is False
    ctx = _ctx()
    handle = bridge.trigger(ctx)
    assert isinstance(handle, ShellRunHandle)


def test_bridge_enabled_flag_is_orthogonal(monkeypatch, bridge):
    """Setting the flag to 1 doesn't break the bridge itself."""
    monkeypatch.setenv("OPENCLAW_SHELL_ENABLED", "1")
    ctx = _ctx()
    handle = bridge.trigger(ctx)
    assert isinstance(handle, ShellRunHandle)
