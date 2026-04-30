# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
tuning/compare.py — compare backends/lanes using retained ExecutionRecord evidence.

compare_backends() groups records by (lane, backend) and produces a
BackendComparisonSummary for each group.

Derived dimensions:
  success_rate       — result.success is True
  failure_rate       — status is FAILED, TIMEOUT, or CANCELLED
  partial_rate       — failure_category is NO_CHANGES (completed, nothing changed)
  timeout_rate       — failure_category is TIMEOUT or status is TIMEOUT
  validation_pass_rate   — validation status is PASSED
  validation_skip_rate   — validation status is SKIPPED
  change_evidence_class  — distribution of ChangedFilesEvidence.status
  latency_class          — from metadata["duration_ms"] if present
  reliability_class      — from success_rate

Limitations that callers should be aware of:
  - latency_class requires duration_ms in ExecutionRecord.metadata; it is
    often UNKNOWN because RunTelemetry is not retained in ExecutionRecord.
  - validation_pass_rate is 0.0 when all runs skip validation.
  - small sample sizes produce WEAK evidence regardless of measured rates.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Optional

from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory, ValidationStatus
from operations_center.observability.changed_files import ChangedFilesStatus
from operations_center.observability.models import ExecutionRecord

from .routing_models import (
    BackendComparisonSummary,
    ChangeEvidenceClass,
    EvidenceStrength,
    LatencyClass,
    ReliabilityClass,
)

# Thresholds for evidence strength based on sample count.
_MIN_STRONG = 20
_MIN_MODERATE = 8

# Thresholds for reliability classification.
_HIGH_SUCCESS_RATE = 0.85
_LOW_SUCCESS_RATE = 0.60

# Thresholds for change evidence class.
_STRONG_EVIDENCE_RATE = 0.80
_PARTIAL_EVIDENCE_RATE = 0.40

# Latency thresholds (milliseconds).
_FAST_MS = 30_000
_SLOW_MS = 120_000


def compare_backends(
    records: list[ExecutionRecord],
    task_type_scope: list[str] | None = None,
    risk_scope: list[str] | None = None,
) -> list[BackendComparisonSummary]:
    """Build one BackendComparisonSummary per distinct (lane, backend) pair.

    Args:
        records:         All ExecutionRecords to analyze.
        task_type_scope: Optional filter; if provided, only include records whose
                         metadata["task_type"] matches. Empty means all records.
        risk_scope:      Optional filter; if provided, only include records whose
                         metadata["risk_level"] matches.

    Returns:
        A list of BackendComparisonSummary, one per (lane, backend) group.
        Empty list if no records.
    """
    filtered = _filter_records(records, task_type_scope, risk_scope)
    if not filtered:
        return []

    groups: dict[tuple[str, str], list[ExecutionRecord]] = defaultdict(list)
    for record in filtered:
        key = (record.lane or "unknown", record.backend or "unknown")
        groups[key].append(record)

    return [
        _build_summary(lane, backend, group_records, task_type_scope or [], risk_scope or [])
        for (lane, backend), group_records in sorted(groups.items())
    ]


def compare_by_task_type(
    records: list[ExecutionRecord],
) -> list[BackendComparisonSummary]:
    """Build comparison summaries split by (lane, backend, task_type).

    Uses metadata["task_type"] if present; groups without it appear under
    lane/backend with task_type_scope=["unknown"].
    """
    if not records:
        return []

    groups: dict[tuple[str, str, str], list[ExecutionRecord]] = defaultdict(list)
    for record in records:
        task_type = str(record.metadata.get("task_type", "unknown"))
        key: tuple[str, str, str] = (record.lane or "unknown", record.backend or "unknown", task_type)
        groups[key].append(record)

    summaries = []
    for (lane, backend, task_type), group_records in sorted(groups.items()):
        summary = _build_summary(lane, backend, group_records, [task_type], [])
        summaries.append(summary)
    return summaries


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _filter_records(
    records: list[ExecutionRecord],
    task_type_scope: list[str] | None,
    risk_scope: list[str] | None,
) -> list[ExecutionRecord]:
    result = records
    if task_type_scope:
        result = [r for r in result if r.metadata.get("task_type", "") in task_type_scope]
    if risk_scope:
        result = [r for r in result if r.metadata.get("risk_level", "") in risk_scope]
    return result


def _build_summary(
    lane: str,
    backend: str,
    records: list[ExecutionRecord],
    task_type_scope: list[str],
    risk_scope: list[str],
) -> BackendComparisonSummary:
    n = len(records)
    if n == 0:
        return BackendComparisonSummary(
            backend=backend,
            lane=lane,
            task_type_scope=task_type_scope,
            risk_scope=risk_scope,
            sample_size=0,
            evidence_strength=EvidenceStrength.WEAK,
            success_rate=0.0,
            failure_rate=0.0,
            partial_rate=0.0,
            timeout_rate=0.0,
            validation_pass_rate=0.0,
            validation_skip_rate=0.0,
            reliability_class=ReliabilityClass.LOW,
            change_evidence_class=ChangeEvidenceClass.UNKNOWN,
            notes="No records available for this group.",
        )

    success_count = sum(1 for r in records if r.result.success)
    failure_count = sum(
        1 for r in records
        if r.result.status in (ExecutionStatus.FAILED, ExecutionStatus.CANCELLED)
        or r.result.failure_category == FailureReasonCategory.TIMEOUT
    )
    partial_count = sum(
        1 for r in records
        if r.result.failure_category == FailureReasonCategory.NO_CHANGES
    )
    timeout_count = sum(
        1 for r in records
        if r.result.failure_category == FailureReasonCategory.TIMEOUT
        or r.result.status == ExecutionStatus.TIMED_OUT
    )

    val_pass_count = sum(
        1 for r in records
        if r.validation_evidence.status == ValidationStatus.PASSED
    )
    val_skip_count = sum(
        1 for r in records
        if r.validation_evidence.status == ValidationStatus.SKIPPED
    )

    evidence_strength = _evidence_strength(n)
    success_rate = round(success_count / n, 3)
    failure_rate = round(failure_count / n, 3)
    partial_rate = round(partial_count / n, 3)
    timeout_rate = round(timeout_count / n, 3)
    validation_pass_rate = round(val_pass_count / n, 3)
    validation_skip_rate = round(val_skip_count / n, 3)

    reliability_class = _reliability_class(success_rate)
    change_evidence_class = _change_evidence_class(records)
    latency_class, median_duration_ms = _latency_class(records)

    return BackendComparisonSummary(
        backend=backend,
        lane=lane,
        task_type_scope=task_type_scope,
        risk_scope=risk_scope,
        sample_size=n,
        evidence_strength=evidence_strength,
        success_rate=success_rate,
        failure_rate=failure_rate,
        partial_rate=partial_rate,
        timeout_rate=timeout_rate,
        validation_pass_rate=validation_pass_rate,
        validation_skip_rate=validation_skip_rate,
        latency_class=latency_class,
        median_duration_ms=median_duration_ms,
        reliability_class=reliability_class,
        change_evidence_class=change_evidence_class,
    )


def _evidence_strength(sample_size: int) -> EvidenceStrength:
    if sample_size >= _MIN_STRONG:
        return EvidenceStrength.STRONG
    if sample_size >= _MIN_MODERATE:
        return EvidenceStrength.MODERATE
    return EvidenceStrength.WEAK


def _reliability_class(success_rate: float) -> ReliabilityClass:
    if success_rate >= _HIGH_SUCCESS_RATE:
        return ReliabilityClass.HIGH
    if success_rate >= _LOW_SUCCESS_RATE:
        return ReliabilityClass.MEDIUM
    return ReliabilityClass.LOW


def _change_evidence_class(records: list[ExecutionRecord]) -> ChangeEvidenceClass:
    if not records:
        return ChangeEvidenceClass.UNKNOWN

    # Count runs with KNOWN or NONE (reliable evidence)
    reliable_count = sum(
        1 for r in records
        if r.changed_files_evidence.status in (ChangedFilesStatus.KNOWN, ChangedFilesStatus.NONE)
    )
    applicable_count = sum(
        1 for r in records
        if r.changed_files_evidence.status != ChangedFilesStatus.NOT_APPLICABLE
    )

    if applicable_count == 0:
        return ChangeEvidenceClass.UNKNOWN

    rate = reliable_count / applicable_count
    if rate >= _STRONG_EVIDENCE_RATE:
        return ChangeEvidenceClass.STRONG
    if rate >= _PARTIAL_EVIDENCE_RATE:
        return ChangeEvidenceClass.PARTIAL
    return ChangeEvidenceClass.POOR


def _latency_class(
    records: list[ExecutionRecord],
) -> tuple[LatencyClass, Optional[int]]:
    """Derive latency class from metadata["duration_ms"] if available."""
    durations: list[int] = []
    for r in records:
        raw = r.metadata.get("duration_ms")
        if raw is not None:
            try:
                durations.append(int(str(raw)))
            except (TypeError, ValueError):
                pass

    if not durations:
        return LatencyClass.UNKNOWN, None

    median = int(statistics.median(durations))
    if median < _FAST_MS:
        return LatencyClass.FAST, median
    if median <= _SLOW_MS:
        return LatencyClass.MEDIUM, median
    return LatencyClass.SLOW, median
