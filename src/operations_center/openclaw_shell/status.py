# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
openclaw_shell/status.py — status derivation functions.

Converts canonical internal records into shell-facing summaries.
These are pure functions — no side effects, no I/O.

Direction is strictly inward-to-outward:
  ExecutionResult / ExecutionRecord / ExecutionTrace → shell models

Never the other direction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from operations_center.contracts.execution import ExecutionResult

from operations_center.observability.models import ExecutionRecord
from operations_center.observability.trace import ExecutionTrace
from operations_center.observability.changed_files import normalize_changed_files

from .models import ShellInspectionResult, ShellStatusSummary


def status_from_record(
    record: ExecutionRecord,
    trace: ExecutionTrace,
) -> ShellStatusSummary:
    """Derive a ShellStatusSummary from an ExecutionRecord + ExecutionTrace.

    The trace provides the headline/summary text. The record provides
    the canonical status, identifiers, lane/backend, and artifact counts.
    """
    result = record.result
    art_count = (
        len(record.artifact_index.primary_artifacts)
        + len(record.artifact_index.supplemental_artifacts)
    )
    return ShellStatusSummary(
        run_id=record.run_id,
        proposal_id=record.proposal_id,
        decision_id=record.decision_id,
        status=result.status.value,
        success=result.success,
        headline=trace.headline,
        summary=trace.summary,
        selected_lane=record.lane,
        selected_backend=record.backend,
        changed_files_status=record.changed_files_evidence.status.value,
        validation_status=record.validation_evidence.status.value,
        artifact_count=art_count,
        recorded_at=record.recorded_at,
    )


def status_from_result_only(
    result: "ExecutionResult",
    lane: Optional[str] = None,
    backend: Optional[str] = None,
) -> ShellStatusSummary:
    """Derive a lightweight ShellStatusSummary from a bare ExecutionResult.

    Used when the full observability record is not available.
    Headline and summary are synthesized minimally from result fields.
    """

    headline = _minimal_headline(result, lane, backend)
    summary = _minimal_summary(result)

    return ShellStatusSummary(
        run_id=result.run_id,
        proposal_id=result.proposal_id,
        decision_id=result.decision_id,
        status=result.status.value,
        success=result.success,
        headline=headline,
        summary=summary,
        selected_lane=lane,
        selected_backend=backend,
        changed_files_status=normalize_changed_files(result).status.value,
        validation_status=result.validation.status.value,
        artifact_count=len(result.artifacts),
        recorded_at=None,
    )


def inspection_from_record(
    record: ExecutionRecord,
    trace: ExecutionTrace,
) -> ShellInspectionResult:
    """Derive a ShellInspectionResult from an ExecutionRecord + ExecutionTrace.

    Gives the operator a complete picture without exposing backend-native internals.
    All fields derive from the observability layer.
    """
    result = record.result
    art_count = (
        len(record.artifact_index.primary_artifacts)
        + len(record.artifact_index.supplemental_artifacts)
    )
    return ShellInspectionResult(
        run_id=record.run_id,
        proposal_id=record.proposal_id,
        decision_id=record.decision_id,
        status=result.status.value,
        headline=trace.headline,
        summary=trace.summary,
        warnings=list(trace.warnings),
        artifact_count=art_count,
        primary_artifact_count=len(record.artifact_index.primary_artifacts),
        changed_files_status=record.changed_files_evidence.status.value,
        validation_status=record.validation_evidence.status.value,
        backend_detail_count=len(record.backend_detail_refs),
        selected_lane=record.lane,
        selected_backend=record.backend,
        trace_id=trace.trace_id,
        record_id=record.record_id,
        recorded_at=record.recorded_at,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_headline(result: "ExecutionResult", lane: Optional[str], backend: Optional[str]) -> str:
    status = result.status.value.upper()
    parts: list[str] = [status]
    if backend:
        parts.append(backend)
    if lane:
        parts.append(f"@ {lane}")
    parts.append(f"run={result.run_id[:8]}")
    return " | ".join(parts)


def _minimal_summary(result: "ExecutionResult") -> str:
    parts = [f"Run {result.run_id[:8]}"]
    if result.changed_files:
        parts.append(f"changed {len(result.changed_files)} file(s)")
    if result.failure_reason:
        parts.append(f"failed: {result.failure_reason[:80]}")
    return "; ".join(parts)
