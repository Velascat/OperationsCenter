# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""G-V03 — ExecutionTrace forwards runtime_invocation_ref + routing metadata.

A trace consumer should be able to answer "which RxP invocation? which
SwitchBoard rule?" from the trace alone, without re-reading the
ExecutionRecord.
"""

from __future__ import annotations

from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import (
    BackendName,
    ExecutionStatus,
    LaneName,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionResult, RuntimeInvocationRef
from operations_center.observability.recorder import ExecutionRecorder
from operations_center.observability.service import ExecutionObservabilityService


def _result(*, with_ref: bool) -> ExecutionResult:
    ref = (
        RuntimeInvocationRef(
            invocation_id="iv-trace-1",
            runtime_name="direct_local",
            runtime_kind="subprocess",
            stdout_path="/tmp/x/stdout.txt",
            stderr_path="/tmp/x/stderr.txt",
            artifact_directory="/tmp/x",
        )
        if with_ref
        else None
    )
    return ExecutionResult(
        run_id="run-trace-1",
        proposal_id="prop",
        decision_id="dec",
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        runtime_invocation_ref=ref,
    )


def test_trace_forwards_runtime_invocation_ref() -> None:
    record = ExecutionRecorder().record(_result(with_ref=True), backend="direct_local", lane="aider_local")
    trace = ExecutionObservabilityService.default()._report_builder.build_report(record)

    assert trace.runtime_invocation_ref is not None
    assert trace.runtime_invocation_ref.invocation_id == "iv-trace-1"
    assert trace.runtime_invocation_ref.runtime_name == "direct_local"
    assert trace.runtime_invocation_ref.stdout_path == "/tmp/x/stdout.txt"


def test_trace_runtime_invocation_ref_is_none_when_absent() -> None:
    record = ExecutionRecorder().record(_result(with_ref=False), backend="demo_stub", lane="aider_local")
    trace = ExecutionObservabilityService.default()._report_builder.build_report(record)
    assert trace.runtime_invocation_ref is None


def test_trace_forwards_routing_block_from_record_metadata() -> None:
    routing = {
        "decision_id": "dec-trace-1",
        "selected_lane": LaneName.AIDER_LOCAL.value,
        "selected_backend": BackendName.DIRECT_LOCAL.value,
        "policy_rule_matched": "lint_fix_to_aider_local",
        "rationale": "lint_fix tasks default to aider_local",
        "switchboard_version": "0.4.2",
        "confidence": 0.87,
        "alternatives_considered": ["claude_cli"],
    }
    record = ExecutionRecorder().record(
        _result(with_ref=True),
        backend="direct_local",
        lane="aider_local",
        metadata={"routing": routing, "task_type": "lint_fix"},
    )
    trace = ExecutionObservabilityService.default()._report_builder.build_report(record)

    assert trace.routing == routing


def test_trace_routing_is_empty_when_record_has_none() -> None:
    record = ExecutionRecorder().record(_result(with_ref=False), backend="x", lane="y")
    trace = ExecutionObservabilityService.default()._report_builder.build_report(record)
    assert trace.routing == {}


def test_trace_round_trips_through_json() -> None:
    record = ExecutionRecorder().record(
        _result(with_ref=True),
        backend="direct_local",
        lane="aider_local",
        metadata={"routing": {"decision_id": "d", "selected_lane": "aider_local"}},
    )
    trace = ExecutionObservabilityService.default()._report_builder.build_report(record)
    blob = trace.model_dump_json()
    assert "iv-trace-1" in blob
    assert "decision_id" in blob
