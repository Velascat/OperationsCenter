"""Tests for observability/service.py — ExecutionObservabilityService."""

from __future__ import annotations

import pytest

from control_plane.contracts.enums import ExecutionStatus
from control_plane.observability.models import BackendDetailRef, ExecutionRecord
from control_plane.observability.service import ExecutionObservabilityService
from control_plane.observability.trace import ExecutionTrace

from .conftest import (
    successful_rich_result,
    failed_result_with_logs,
    sparse_result,
)


@pytest.fixture
def svc() -> ExecutionObservabilityService:
    return ExecutionObservabilityService.default()


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


def test_observe_returns_tuple(svc, successful_rich_result):
    result = svc.observe(successful_rich_result)
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_observe_returns_record_and_trace(svc, successful_rich_result):
    record, trace = svc.observe(successful_rich_result)
    assert isinstance(record, ExecutionRecord)
    assert isinstance(trace, ExecutionTrace)


# ---------------------------------------------------------------------------
# Record and trace are linked
# ---------------------------------------------------------------------------


def test_trace_record_id_matches_record(svc, successful_rich_result):
    record, trace = svc.observe(successful_rich_result)
    assert trace.record_id == record.record_id


def test_record_result_matches_input(svc, successful_rich_result):
    record, _ = svc.observe(successful_rich_result)
    assert record.result is successful_rich_result


# ---------------------------------------------------------------------------
# Metadata propagation
# ---------------------------------------------------------------------------


def test_backend_propagated(svc, successful_rich_result):
    record, trace = svc.observe(successful_rich_result, backend="kodo")
    assert record.backend == "kodo"
    assert "kodo" in trace.headline


def test_lane_propagated(svc, successful_rich_result):
    record, trace = svc.observe(successful_rich_result, lane="aider_local")
    assert record.lane == "aider_local"
    assert "aider_local" in trace.headline


def test_raw_detail_refs_propagated(svc, successful_rich_result):
    ref = BackendDetailRef(detail_type="stderr_log", path="/tmp/stderr.txt")
    record, trace = svc.observe(successful_rich_result, raw_detail_refs=[ref])
    assert len(record.backend_detail_refs) == 1
    assert len(trace.backend_detail_refs) == 1


def test_notes_propagated(svc, successful_rich_result):
    record, _ = svc.observe(successful_rich_result, notes="cron-trigger")
    assert record.notes == "cron-trigger"


def test_metadata_propagated(svc, successful_rich_result):
    record, _ = svc.observe(successful_rich_result, metadata={"env": "prod"})
    assert record.metadata["env"] == "prod"


# ---------------------------------------------------------------------------
# Works with all fixture scenarios
# ---------------------------------------------------------------------------


def test_works_with_failed_result(svc, failed_result_with_logs):
    record, trace = svc.observe(failed_result_with_logs)
    assert record.result.success is False
    assert trace.status == ExecutionStatus.FAILED


def test_works_with_sparse_result(svc, sparse_result):
    record, trace = svc.observe(sparse_result)
    assert isinstance(record, ExecutionRecord)
    assert isinstance(trace, ExecutionTrace)


# ---------------------------------------------------------------------------
# Unique IDs per call
# ---------------------------------------------------------------------------


def test_each_observe_produces_unique_record_id(svc, successful_rich_result):
    r1, _ = svc.observe(successful_rich_result)
    r2, _ = svc.observe(successful_rich_result)
    assert r1.record_id != r2.record_id


def test_each_observe_produces_unique_trace_id(svc, successful_rich_result):
    _, t1 = svc.observe(successful_rich_result)
    _, t2 = svc.observe(successful_rich_result)
    assert t1.trace_id != t2.trace_id


# ---------------------------------------------------------------------------
# Default constructor
# ---------------------------------------------------------------------------


def test_default_creates_service():
    svc = ExecutionObservabilityService.default()
    assert isinstance(svc, ExecutionObservabilityService)


def test_with_client_injection():
    from control_plane.observability.recorder import ExecutionRecorder
    from control_plane.observability.trace import RunReportBuilder

    svc = ExecutionObservabilityService(
        recorder=ExecutionRecorder(),
        report_builder=RunReportBuilder(),
    )
    assert isinstance(svc, ExecutionObservabilityService)
