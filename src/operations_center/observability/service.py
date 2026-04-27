"""
observability/service.py — ExecutionObservabilityService.

Top-level facade for the observability pipeline:

    ExecutionResult + raw detail refs
        → ExecutionRecorder → ExecutionRecord
        → RunReportBuilder  → ExecutionTrace

Typical usage:

    svc = ExecutionObservabilityService.default()
    record, trace = svc.observe(result, backend="kodo", lane="claude_cli")

Both record and trace are returned so the caller can retain the record and
display/log the trace independently.
"""

from __future__ import annotations

from typing import Any, Optional

from operations_center.contracts.execution import ExecutionResult

from .models import BackendDetailRef, ExecutionRecord
from .recorder import ExecutionRecorder
from .trace import ExecutionTrace, RunReportBuilder


class ExecutionObservabilityService:
    """Orchestrates execution recording and trace generation."""

    def __init__(
        self,
        recorder: Optional[ExecutionRecorder] = None,
        report_builder: Optional[RunReportBuilder] = None,
    ) -> None:
        self._recorder = recorder or ExecutionRecorder()
        self._report_builder = report_builder or RunReportBuilder()

    def observe(
        self,
        result: ExecutionResult,
        backend: Optional[str] = None,
        lane: Optional[str] = None,
        raw_detail_refs: Optional[list[BackendDetailRef]] = None,
        notes: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> tuple[ExecutionRecord, ExecutionTrace]:
        """Record an execution result and build an inspectable trace.

        Returns:
            (ExecutionRecord, ExecutionTrace) — retain the record,
            display/log the trace.
        """
        record = self._recorder.record(
            result,
            backend=backend,
            lane=lane,
            raw_detail_refs=raw_detail_refs,
            notes=notes,
            metadata=metadata,
        )
        trace = self._report_builder.build_report(record)
        return record, trace

    @classmethod
    def default(cls) -> "ExecutionObservabilityService":
        return cls()
