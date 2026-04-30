# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
observability/trace.py — ExecutionTrace and RunReportBuilder.

ExecutionTrace is an inspectable, report-oriented view of one execution run.
It is built from an ExecutionRecord by RunReportBuilder and is the primary
artifact for human-readable summaries, logs, and downstream triage.

Key design rule: ExecutionTrace is never persisted as the primary record.
ExecutionRecord is. ExecutionTrace is derived on demand from ExecutionRecord.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.contracts.execution import ExecutionArtifact

from .changed_files import ChangedFilesStatus
from .models import BackendDetailRef, ExecutionRecord
from .validation import ValidationEvidence


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class ExecutionTrace(BaseModel):
    """Inspectable report-oriented view of an execution run.

    Generated from ExecutionRecord. Suitable for logging, display, and
    triage. Does not replace ExecutionRecord as the retained record.
    """

    trace_id: str = Field(default_factory=_new_id)
    record_id: str
    headline: str = Field(description="One-line status summary: STATUS | backend @ lane | run=…")
    status: ExecutionStatus
    summary: str = Field(description="Human-readable prose summary of the run outcome.")
    key_artifacts: list[ExecutionArtifact] = Field(
        default_factory=list,
        description="Primary artifacts from the run (diff, patch, validation report).",
    )
    changed_files_summary: str = Field(
        default="",
        description="Short prose description of changed-file evidence.",
    )
    validation_summary: ValidationEvidence
    warnings: list[str] = Field(default_factory=list)
    backend_detail_refs: list[BackendDetailRef] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": True}


class RunReportBuilder:
    """Builds an ExecutionTrace from an ExecutionRecord."""

    def build_report(self, record: ExecutionRecord) -> ExecutionTrace:
        return ExecutionTrace(
            record_id=record.record_id,
            headline=self._headline(record),
            status=record.result.status,
            summary=self._summary(record),
            key_artifacts=list(record.artifact_index.primary_artifacts),
            changed_files_summary=self._changed_files_summary(record),
            validation_summary=record.validation_evidence,
            warnings=self._warnings(record),
            backend_detail_refs=list(record.backend_detail_refs),
        )

    # ------------------------------------------------------------------

    def _headline(self, record: ExecutionRecord) -> str:
        status = record.result.status.value.upper()
        backend = record.backend or "unknown"
        lane = record.lane or "unknown"
        return f"{status} | {backend} @ {lane} | run={record.run_id[:8]}"

    def _summary(self, record: ExecutionRecord) -> str:
        result = record.result
        parts: list[str] = [f"Run {result.run_id[:8]}"]

        if result.success:
            cfe = record.changed_files_evidence
            if cfe.status == ChangedFilesStatus.KNOWN:
                n = len(cfe.files)
                parts.append(f"changed {n} {'file' if n == 1 else 'files'}")
            elif cfe.status == ChangedFilesStatus.INFERRED:
                n = len(cfe.files)
                parts.append(f"changed {n} {'file' if n == 1 else 'files'} (inferred)")
            elif cfe.status == ChangedFilesStatus.NONE:
                parts.append("completed with no file changes")
            else:
                parts.append("succeeded")
            if result.diff_stat_excerpt:
                parts.append(result.diff_stat_excerpt)
        else:
            if result.failure_reason:
                reason = result.failure_reason[:120]
                parts.append(f"failed: {reason}")
            elif result.failure_category:
                parts.append(f"failed ({result.failure_category.value})")
            else:
                parts.append(f"failed ({result.status.value})")

        val = record.validation_evidence
        if val.status.value not in ("skipped",):
            parts.append(
                f"validation={val.status.value} ({val.checks_passed}/{val.checks_run} passed)"
            )

        return "; ".join(parts)

    def _changed_files_summary(self, record: ExecutionRecord) -> str:
        cfe = record.changed_files_evidence
        if cfe.status == ChangedFilesStatus.KNOWN:
            n = len(cfe.files)
            return f"{n} {'file' if n == 1 else 'files'} changed (source: {cfe.source})"
        if cfe.status == ChangedFilesStatus.INFERRED:
            n = len(cfe.files)
            return f"{n} {'file' if n == 1 else 'files'} changed (inferred from {cfe.source})"
        if cfe.status == ChangedFilesStatus.NONE:
            return f"no files changed (source: {cfe.source})"
        if cfe.status == ChangedFilesStatus.NOT_APPLICABLE:
            return "not applicable (execution did not run)"
        return "unknown (backend did not report changed files)"

    def _warnings(self, record: ExecutionRecord) -> list[str]:
        warnings: list[str] = []
        cfe = record.changed_files_evidence
        if cfe.status == ChangedFilesStatus.INFERRED:
            warnings.append(
                "changed-file manifest is inferred from backend event data and is not authoritative"
            )
        if cfe.status == ChangedFilesStatus.UNKNOWN:
            warnings.append(
                "changed-file manifest unavailable; backend did not report file changes"
            )
        val = record.validation_evidence
        if val.status.value == "skipped":
            warnings.append("validation was skipped for this run")
        if not record.artifact_index.primary_artifacts:
            warnings.append("no primary artifacts produced by this run")
        if record.result.failure_category == FailureReasonCategory.NO_CHANGES:
            warnings.append("run completed with no file changes")
        return warnings
