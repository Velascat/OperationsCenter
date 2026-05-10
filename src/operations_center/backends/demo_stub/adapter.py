# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/demo_stub/adapter.py — deterministic stub backend for demo and integration tests.

DemoStubBackendAdapter accepts a canonical ExecutionRequest and produces a
deterministic ExecutionResult without any external service calls, CLI tools,
git operations, or network access.

Behaviour contract:
  - Writes artifacts/demo_result.txt inside request.workspace_path.
  - Returns ExecutionResult with status=SUCCEEDED and success=True.
  - Never raises; errors are returned as failed ExecutionResult values.
  - Does not import from SwitchBoard, Kodo, Archon, or any live adapter.

Intended use:
  - OperationsCenter demo entrypoint (--backend stub).
  - Contract tests for ExecutionCoordinator that need a real (non-mocked)
    adapter to prove the full boundary.
  - Policy-blocked flow tests (combine with a blocking PolicyEngine).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from operations_center.contracts.common import ChangedFileRef, ValidationSummary
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import (
    ExecutionArtifact,
    ExecutionRequest,
    ExecutionResult,
)

ARTIFACT_FILENAME = "demo_result.txt"
BACKEND_LABEL = "demo_stub"


class DemoStubBackendAdapter:
    """Deterministic stub backend.  Writes one artifact; returns SUCCEEDED."""

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        try:
            artifact_path = self._write_artifact(request)
        except OSError as exc:
            return ExecutionResult(
                run_id=request.run_id,
                proposal_id=request.proposal_id,
                decision_id=request.decision_id,
                status=ExecutionStatus.FAILED,
                success=False,
                validation=ValidationSummary(status=ValidationStatus.SKIPPED),
                failure_category=FailureReasonCategory.BACKEND_ERROR,
                failure_reason=f"DemoStubBackendAdapter could not write artifact: {exc}",
            )

        artifact = ExecutionArtifact(
            artifact_type=ArtifactType.LOG_EXCERPT,
            label="demo artifact",
            uri=str(artifact_path),
        )

        return ExecutionResult(
            run_id=request.run_id,
            proposal_id=request.proposal_id,
            decision_id=request.decision_id,
            status=ExecutionStatus.SUCCEEDED,
            success=True,
            changed_files=[
                ChangedFileRef(
                    path=f"artifacts/{ARTIFACT_FILENAME}",
                    change_type="added",
                    lines_added=6,
                    lines_removed=0,
                )
            ],
            changed_files_source="backend_manifest",
            changed_files_confidence=1.0,
            diff_stat_excerpt="1 file changed, 6 insertions(+)",
            validation=ValidationSummary(status=ValidationStatus.SKIPPED),
            artifacts=[artifact],
            completed_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------

    def _write_artifact(self, request: ExecutionRequest) -> Path:
        artifacts_dir = Path(request.workspace_path) / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifacts_dir / ARTIFACT_FILENAME
        artifact_path.write_text(
            f"OperationsCenter demo execution\n"
            f"run_id:    {request.run_id}\n"
            f"goal:      {request.goal_text}\n"
            f"repo_key:  {request.repo_key}\n"
            f"backend:   {BACKEND_LABEL}\n"
            f"completed: {datetime.now(timezone.utc).isoformat()}\n",
            encoding="utf-8",
        )
        return artifact_path
