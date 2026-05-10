# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
observability/changed_files.py — Changed-file evidence with honest uncertainty.

Three states are supported:
  KNOWN          — files enumerated by the backend
  INFERRED       — files present but provenance is indirect / lower-confidence
  NONE           — backend confirmed no files changed (e.g. NO_CHANGES outcome)
  UNKNOWN        — backend did not report; could not determine
  NOT_APPLICABLE — execution never ran (e.g. policy_blocked, unsupported_request)

Do not coerce UNKNOWN or INFERRED into KNOWN. Downstream code must handle uncertainty.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from operations_center.contracts.common import ChangedFileRef
from operations_center.contracts.enums import FailureReasonCategory
from operations_center.contracts.execution import ExecutionResult


class ChangedFilesStatus(str, Enum):
    KNOWN = "known"
    INFERRED = "inferred"
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
                    "backend_confirmed_empty, policy_blocked, adapter_unsupported, or none.",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    notes: Optional[str] = None

    model_config = {"frozen": True}


def normalize_changed_files(result: ExecutionResult) -> ChangedFilesEvidence:
    """Derive ChangedFilesEvidence from a canonical ExecutionResult."""
    source = (result.changed_files_source or "").strip()
    confidence = result.changed_files_confidence

    if result.failure_category == FailureReasonCategory.POLICY_BLOCKED:
        return ChangedFilesEvidence(
            status=ChangedFilesStatus.NOT_APPLICABLE,
            source="policy_blocked",
            confidence=1.0,
            notes="Execution was blocked by policy; no files were changed.",
        )

    if result.failure_category == FailureReasonCategory.UNSUPPORTED_REQUEST:
        return ChangedFilesEvidence(
            status=ChangedFilesStatus.NOT_APPLICABLE,
            source="adapter_unsupported",
            confidence=1.0,
            notes="Execution did not run because the selected adapter could not support the request.",
        )

    if result.failure_category == FailureReasonCategory.NO_CHANGES:
        return ChangedFilesEvidence(
            status=ChangedFilesStatus.NONE,
            source=source or "backend_confirmed_empty",
            confidence=confidence if confidence is not None else 1.0,
            notes="Execution completed with authoritative evidence of no file changes.",
        )

    if source in {"git_diff", "backend_manifest"} and result.changed_files:
        return ChangedFilesEvidence(
            status=ChangedFilesStatus.KNOWN,
            files=list(result.changed_files),
            source=source,
            confidence=confidence if confidence is not None else 1.0,
        )

    if source in {"git_diff", "backend_manifest"} and not result.changed_files:
        return ChangedFilesEvidence(
            status=ChangedFilesStatus.NONE,
            source=source,
            confidence=confidence if confidence is not None else 1.0,
            notes="Authoritative diff evidence confirms no files were changed.",
        )

    if source == "event_stream" and result.changed_files:
        return ChangedFilesEvidence(
            status=ChangedFilesStatus.INFERRED,
            files=list(result.changed_files),
            source=source,
            confidence=confidence if confidence is not None else 0.5,
            notes="Changed-file list was inferred from backend event data.",
        )

    if result.changed_files:
        return ChangedFilesEvidence(
            status=ChangedFilesStatus.UNKNOWN,
            files=list(result.changed_files),
            source=source or "unspecified",
            confidence=confidence if confidence is not None else 0.0,
            notes=(
                "Changed-file list was present without trustworthy provenance; "
                "certainty was not upgraded."
            ),
        )

    return ChangedFilesEvidence(
        status=ChangedFilesStatus.UNKNOWN,
        source=source or "none",
        confidence=confidence if confidence is not None else 0.0,
        notes="Backend did not report changed files.",
    )
