# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Phase 8 behavior calibration models.

All output types (findings, recommendations, report) are immutable Pydantic
models so they can be serialized to JSON and shared safely.

BehaviorCalibrationInput is a plain dataclass because it holds a
ManagedArtifactIndex (itself a dataclass) and is not serialized.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AnalysisProfile(str, Enum):
    """Explicit analysis profile requested by the caller."""
    SUMMARY = "summary"
    FAILURE_DIAGNOSIS = "failure_diagnosis"
    COVERAGE_GAPS = "coverage_gaps"
    ARTIFACT_HEALTH = "artifact_health"
    PRODUCER_COMPLIANCE = "producer_compliance"
    RECOMMENDATION = "recommendation"


class FindingSeverity(str, Enum):
    """Severity level of a calibration finding."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class FindingCategory(str, Enum):
    """Category of a calibration finding."""
    MISSING_ARTIFACT = "missing_artifact"
    PARTIAL_RUN = "partial_run"
    FAILED_RUN = "failed_run"
    UNRESOLVED_PATH = "unresolved_path"
    MISSING_FILE = "missing_file"
    INVALID_JSON = "invalid_json"
    PRODUCER_CONTRACT_GAP = "producer_contract_gap"
    COVERAGE_GAP = "coverage_gap"
    RUNTIME_FAILURE = "runtime_failure"
    REPO_SINGLETON_WARNING = "repo_singleton_warning"
    NOISE_EXCLUSION = "noise_exclusion"
    UNKNOWN = "unknown"


class RecommendationPriority(str, Enum):
    """Priority level of a calibration recommendation."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

@dataclass
class BehaviorCalibrationInput:
    """Input for a calibration analysis run.

    artifact_index is required. analysis_profile must be explicit.
    Artifact content reading is opt-in and guarded by max_artifact_bytes.
    """

    repo_id: str
    run_id: str
    audit_type: str
    artifact_index: Any  # ManagedArtifactIndex — not imported here to avoid circular refs
    analysis_profile: AnalysisProfile
    metadata: dict[str, Any] = field(default_factory=dict)
    dispatch_result: Any | None = None
    selected_artifact_ids: list[str] | None = None  # None = analyze all artifacts
    max_artifact_bytes: int = 10 * 1024 * 1024  # 10 MiB
    include_artifact_content: bool = False  # opt-in content analysis


# ---------------------------------------------------------------------------
# Output types (Pydantic, serializable)
# ---------------------------------------------------------------------------

class ArtifactIndexSummary(BaseModel, frozen=True):
    """High-level summary of a ManagedArtifactIndex."""

    total_artifacts: int
    by_kind: dict[str, int]
    by_location: dict[str, int]
    by_status: dict[str, int]
    singleton_count: int
    partial_count: int
    excluded_path_count: int
    unresolved_path_count: int
    missing_file_count: int
    machine_readable_count: int
    warnings_count: int
    errors_count: int
    manifest_limitations: list[str]


class CalibrationFinding(BaseModel, frozen=True):
    """A single structured observation from artifact analysis.

    Findings are descriptive, not prescriptive. They describe what was
    observed in the artifact index — they do not prescribe any action.
    """

    finding_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Stable identifier for this finding within the report.",
    )
    severity: FindingSeverity
    category: FindingCategory
    summary: str = Field(description="One-line human-readable description.")
    detail: str = Field(default="", description="Extended explanation of the finding.")
    artifact_ids: list[str] = Field(
        default_factory=list,
        description="Artifact IDs related to this finding.",
    )
    source: str = Field(
        description="Rule or check that produced this finding (e.g. 'check_missing_files').",
    )
    confidence: str = Field(
        default="high",
        description="Confidence in the finding: 'low', 'medium', 'high'.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class CalibrationRecommendation(BaseModel, frozen=True):
    """An advisory tuning suggestion derived from calibration findings.

    Recommendations are ADVISORY ONLY. They must not be applied automatically.
    They require human review before any action is taken.

    A recommendation must never directly:
      - modify managed repo config
      - modify runtime policy
      - modify producer artifacts or manifests
      - modify source code
    """

    recommendation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    priority: RecommendationPriority
    summary: str
    rationale: str
    affected_repo_id: str
    affected_audit_type: str
    affected_artifact_ids: list[str] = Field(default_factory=list)
    suggested_action: str = Field(
        description="Descriptive statement of the suggested human action.",
    )
    risk: str = Field(
        default="low",
        description="Risk level if the suggested action is skipped: 'low', 'medium', 'high'.",
    )
    requires_human_review: bool = Field(
        default=True,
        description="Always True. Recommendations are never applied automatically.",
    )
    supporting_finding_ids: list[str] = Field(
        default_factory=list,
        description="IDs of CalibrationFindings that support this recommendation.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorCalibrationReport(BaseModel):
    """Durable output of a calibration analysis run.

    Serializable to JSON. Written to an OperationsCenter-owned report path.
    """

    schema_version: str = "1.0"
    repo_id: str
    run_id: str
    audit_type: str
    analysis_profile: AnalysisProfile
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    artifact_index_summary: ArtifactIndexSummary
    findings: list[CalibrationFinding] = Field(default_factory=list)
    recommendations: list[CalibrationRecommendation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def recommendation_count(self) -> int:
        return len(self.recommendations)

    @property
    def has_errors(self) -> bool:
        return any(
            f.severity in (FindingSeverity.ERROR, FindingSeverity.CRITICAL)
            for f in self.findings
        )


__all__ = [
    "AnalysisProfile",
    "ArtifactIndexSummary",
    "BehaviorCalibrationInput",
    "BehaviorCalibrationReport",
    "CalibrationFinding",
    "CalibrationRecommendation",
    "FindingCategory",
    "FindingSeverity",
    "RecommendationPriority",
]
