# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 10 slice replay models.

Output types are Pydantic (serializable to JSON).
Input types are plain dataclasses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SliceReplayProfile(str, Enum):
    """Explicit replay profile — determines which checks are executed."""
    FIXTURE_INTEGRITY = "fixture_integrity"
    MANIFEST_CONTRACT = "manifest_contract"
    ARTIFACT_READABILITY = "artifact_readability"
    FAILURE_SLICE = "failure_slice"
    STAGE_SLICE = "stage_slice"
    METADATA_ONLY_SLICE = "metadata_only_slice"


CheckStatus = Literal["passed", "failed", "skipped", "error"]
ReportStatus = Literal["passed", "failed", "error", "partial"]


# ---------------------------------------------------------------------------
# Serializable output models (Pydantic)
# ---------------------------------------------------------------------------

class SliceReplayCheck(BaseModel, frozen=True):
    """Descriptor of a single replay check to be executed.

    Checks are deterministic and local to the fixture pack.
    They never call Phase 6 dispatch, run audits, or import managed repo code.
    """

    check_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    check_type: str = Field(description="The type of check to perform.")
    fixture_artifact_ids: list[str] = Field(
        default_factory=list,
        description="Fixture artifact IDs this check applies to.",
    )
    description: str
    required: bool = Field(
        default=True,
        description="If True, failure causes the overall report status to be 'failed'.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class SliceReplayCheckResult(BaseModel, frozen=True):
    """Result of executing a single SliceReplayCheck."""

    check_id: str
    status: CheckStatus
    fixture_artifact_ids: list[str] = Field(default_factory=list)
    summary: str
    detail: str = ""
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def failed(self) -> bool:
        return self.status == "failed"


class SliceReplayReport(BaseModel):
    """Durable report produced by a slice replay run.

    Serializable to JSON. Written to an OperationsCenter-owned path.
    Does not modify fixture packs or source artifacts.
    """

    schema_version: str = "1.0"
    replay_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    fixture_pack_id: str
    fixture_pack_path: str
    source_repo_id: str
    source_run_id: str
    source_audit_type: str
    replay_profile: SliceReplayProfile
    status: ReportStatus
    summary: str
    check_results: list[SliceReplayCheckResult] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.check_results if r.status == "passed")

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.check_results if r.status == "failed")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.check_results if r.status == "error")

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.check_results if r.status == "skipped")

    @property
    def total_count(self) -> int:
        return len(self.check_results)


# ---------------------------------------------------------------------------
# Input types (dataclasses, not serialized)
# ---------------------------------------------------------------------------

@dataclass
class SliceReplayRequest:
    """Input for a slice replay run.

    fixture_pack_path must point to fixture_pack.json (or its directory).
    replay_profile must be explicit.
    """

    fixture_pack_path: Path
    replay_profile: SliceReplayProfile
    selected_fixture_artifact_ids: list[str] | None = None
    source_stage: str | None = None
    artifact_kind: str | None = None
    max_artifact_bytes: int = 10 * 1024 * 1024  # 10 MiB
    fail_fast: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "CheckStatus",
    "ReportStatus",
    "SliceReplayCheck",
    "SliceReplayCheckResult",
    "SliceReplayProfile",
    "SliceReplayReport",
    "SliceReplayRequest",
]
