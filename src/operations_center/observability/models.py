# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
observability/models.py — BackendDetailRef and ExecutionRecord.

BackendDetailRef
    A bounded reference to raw backend-native detail (stderr, JSONL streams,
    workspace snapshots). This keeps raw data out of canonical telemetry while
    still making it locatable for debugging.

ExecutionRecord
    The retained normalized run record. It wraps the canonical ExecutionResult
    with observability-oriented metadata: classified artifact index, changed-
    file evidence (with honest uncertainty), validation evidence, and refs to
    raw backend detail.

Neither model replaces ExecutionResult. ExecutionResult remains the canonical
outcome contract. ExecutionRecord is the retained, inspectable record.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from operations_center.contracts.execution import ExecutionResult

from .artifacts import ArtifactIndex
from .changed_files import ChangedFilesEvidence
from .validation import ValidationEvidence


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class BackendDetailRef(BaseModel):
    """A reference to raw backend-native output retained separately from canonical data.

    Use path for filesystem artifacts, uri for remote references.
    is_required_for_debug flags artifacts that are essential for incident investigation.
    """

    ref_id: str = Field(default_factory=_new_id)
    detail_type: str = Field(
        description="Category of raw detail: stderr_log, jsonl_stream, workspace_snapshot, "
                    "stdout_log, event_trace, structured_result, or similar.",
    )
    path: Optional[str] = Field(
        default=None,
        description="Filesystem path to the raw artifact.",
    )
    uri: Optional[str] = Field(
        default=None,
        description="URI for remotely stored artifacts.",
    )
    description: Optional[str] = None
    is_required_for_debug: bool = False

    model_config = {"frozen": True}


class ExecutionRecord(BaseModel):
    """Retained normalized run record.

    Wraps the canonical ExecutionResult with observability metadata. Stored
    alongside the raw backend detail refs it references. Suitable for
    filesystem-local retention and later cross-run comparison.
    """

    record_id: str = Field(default_factory=_new_id)
    run_id: str
    proposal_id: str
    decision_id: str

    result: ExecutionResult

    recorded_at: datetime = Field(default_factory=_utcnow)
    backend: Optional[str] = Field(
        default=None,
        description="Backend name (e.g. 'kodo', 'archon', 'openclaw').",
    )
    lane: Optional[str] = Field(
        default=None,
        description="Lane name (e.g. 'claude_cli', 'aider_local').",
    )

    artifact_index: ArtifactIndex
    changed_files_evidence: ChangedFilesEvidence
    validation_evidence: ValidationEvidence

    backend_detail_refs: list[BackendDetailRef] = Field(default_factory=list)
    notes: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)

    model_config = {"frozen": True}
