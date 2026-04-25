"""
observability/recorder.py — ExecutionRecorder.

Assembles a normalized ExecutionRecord from a canonical ExecutionResult plus
optional raw backend detail references.

This is the primary entry point for turning execution outcomes into retained
observability records. It calls the normalizers for artifacts, changed files,
and validation — the caller does not need to invoke those directly.
"""

from __future__ import annotations

from typing import Optional

from operations_center.contracts.execution import ExecutionResult

from .artifacts import ArtifactNormalizer
from .changed_files import normalize_changed_files
from .models import BackendDetailRef, ExecutionRecord
from .validation import normalize_validation


class ExecutionRecorder:
    """Converts an ExecutionResult into a retained ExecutionRecord.

    Usage:
        recorder = ExecutionRecorder()
        record = recorder.record(result, backend="kodo", lane="claude_cli")
    """

    def record(
        self,
        result: ExecutionResult,
        backend: Optional[str] = None,
        lane: Optional[str] = None,
        raw_detail_refs: Optional[list[BackendDetailRef]] = None,
        notes: str = "",
        metadata: Optional[dict[str, object]] = None,
    ) -> ExecutionRecord:
        """Build a normalized ExecutionRecord from a canonical ExecutionResult.

        raw_detail_refs: references to raw backend outputs (stderr logs,
            JSONL streams, workspace snapshots) retained separately from the
            canonical summary. Pass None when no raw detail is available.
        """
        artifact_index = ArtifactNormalizer.index(list(result.artifacts))
        changed_files_evidence = normalize_changed_files(result)
        validation_evidence = normalize_validation(result.validation)

        return ExecutionRecord(
            run_id=result.run_id,
            proposal_id=result.proposal_id,
            decision_id=result.decision_id,
            result=result,
            backend=backend,
            lane=lane,
            artifact_index=artifact_index,
            changed_files_evidence=changed_files_evidence,
            validation_evidence=validation_evidence,
            backend_detail_refs=raw_detail_refs or [],
            notes=notes,
            metadata=metadata or {},
        )
