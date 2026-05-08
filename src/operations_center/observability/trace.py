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
from pathlib import Path

from pydantic import BaseModel, Field

from typing import Any, Optional

from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.contracts.execution import ExecutionArtifact, RuntimeInvocationRef

from .changed_files import ChangedFilesStatus
from .models import BackendDetailRef, ExecutionRecord
from .validation import ValidationEvidence


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _path_exists(path: str) -> bool:
    """Existence probe — isolated so tests can monkeypatch and so any
    OSError (permission, broken symlink, etc.) is treated as 'not present'
    rather than crashing trace build."""
    try:
        return Path(path).exists()
    except OSError:
        return False


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
    # G-V03 — forward the OC ↔ RxP linkage so a single trace has the full
    # provenance chain. None for adapters that do not invoke ExecutorRuntime
    # (e.g. demo_stub).
    runtime_invocation_ref: Optional[RuntimeInvocationRef] = None
    # G-V03 — forward SwitchBoard routing provenance from
    # ExecutionRecord.metadata["routing"]. Empty dict when no routing
    # block was recorded.
    routing: dict[str, Any] = Field(default_factory=dict)
    # Verification Gaps Round 2 — forward SourceRegistry-derived
    # provenance from ExecutionRecord.metadata["provenance"]. Empty
    # dict when the registry didn't carry an entry for this backend
    # (legacy path, registry unavailable, or backend not yet registered).
    provenance: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": True}


class RunReportBuilder:
    """Builds an ExecutionTrace from an ExecutionRecord."""

    def build_report(self, record: ExecutionRecord) -> ExecutionTrace:
        meta = record.metadata if isinstance(record.metadata, dict) else {}
        routing = meta.get("routing")
        provenance = meta.get("provenance")
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
            runtime_invocation_ref=record.result.runtime_invocation_ref,
            routing=dict(routing) if isinstance(routing, dict) else {},
            provenance=dict(provenance) if isinstance(provenance, dict) else {},
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
        # Artifact-path staleness — ExecutorRuntime writes stdout/stderr
        # and an artifact_directory under tmp; if the dir was reaped
        # between the run and the trace build, surface a warning rather
        # than silently shipping a dead path. (We intentionally do NOT
        # error here — stale paths are an operational reality.)
        ref = record.result.runtime_invocation_ref
        if ref is not None:
            for label, path in (
                ("stdout_path", ref.stdout_path),
                ("stderr_path", ref.stderr_path),
                ("artifact_directory", ref.artifact_directory),
            ):
                if isinstance(path, str) and path and not _path_exists(path):
                    warnings.append(
                        f"runtime_invocation_ref.{label} no longer exists on disk: {path}"
                    )
        return warnings
