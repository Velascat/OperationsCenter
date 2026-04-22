"""
observability/changed_files.py — Changed-file evidence with honest uncertainty.

Three states are supported:
  KNOWN          — files enumerated by the backend
  NONE           — backend confirmed no files changed (e.g. NO_CHANGES outcome)
  UNKNOWN        — backend did not report; could not determine
  NOT_APPLICABLE — execution never ran (e.g. policy_blocked)

Do not coerce UNKNOWN into an empty KNOWN. Downstream code must handle uncertainty.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from control_plane.contracts.common import ChangedFileRef
from control_plane.contracts.enums import FailureReasonCategory
from control_plane.contracts.execution import ExecutionResult


class ChangedFilesStatus(str, Enum):
    KNOWN = "known"
    NONE = "none"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class ChangedFilesEvidence(BaseModel):
    """Normalized representation of which files were changed in a run.

    Status encodes certainty level. Never conflate UNKNOWN with NONE.
    """

    status: ChangedFilesStatus
    files: list[ChangedFileRef] = Field(default_factory=list)
    source: str = Field(
        default="",
        description="How changed-file data was obtained: backend_manifest, git_diff, "
                    "backend_confirmed_empty, policy_blocked, or none.",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    notes: Optional[str] = None

    model_config = {"frozen": True}


def normalize_changed_files(result: ExecutionResult) -> ChangedFilesEvidence:
    """Derive ChangedFilesEvidence from a canonical ExecutionResult."""
    if result.failure_category == FailureReasonCategory.POLICY_BLOCKED:
        return ChangedFilesEvidence(
            status=ChangedFilesStatus.NOT_APPLICABLE,
            source="policy_blocked",
            confidence=1.0,
            notes="Execution was blocked by policy; no files were changed.",
        )
    if result.changed_files:
        return ChangedFilesEvidence(
            status=ChangedFilesStatus.KNOWN,
            files=list(result.changed_files),
            source="backend_manifest",
            confidence=1.0,
        )
    if result.failure_category == FailureReasonCategory.NO_CHANGES:
        return ChangedFilesEvidence(
            status=ChangedFilesStatus.NONE,
            source="backend_confirmed_empty",
            confidence=1.0,
            notes="Backend confirmed no files were changed.",
        )
    return ChangedFilesEvidence(
        status=ChangedFilesStatus.UNKNOWN,
        source="none",
        confidence=0.0,
        notes="Backend did not report changed files.",
    )
