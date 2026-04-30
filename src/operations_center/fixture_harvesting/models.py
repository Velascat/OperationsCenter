# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 9 fixture harvesting models.

Output types are Pydantic (serializable to JSON).
Input/intermediate types are plain dataclasses.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from operations_center.behavior_calibration.models import ArtifactIndexSummary


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HarvestProfile(str, Enum):
    """Explicit selection strategy for a fixture pack."""
    MINIMAL_FAILURE = "minimal_failure"
    PARTIAL_RUN = "partial_run"
    ARTIFACT_HEALTH = "artifact_health"
    PRODUCER_COMPLIANCE = "producer_compliance"
    STAGE_SLICE = "stage_slice"
    FULL_MANIFEST_SNAPSHOT = "full_manifest_snapshot"
    MANUAL_SELECTION = "manual_selection"


# ---------------------------------------------------------------------------
# Serializable output models (Pydantic)
# ---------------------------------------------------------------------------

class FixtureFindingReference(BaseModel, frozen=True):
    """Evidence record linking a fixture pack to calibration findings.

    Finding references explain why artifacts were selected. They are
    descriptive only — they must not be treated as executable policy.
    """

    source_finding_id: str
    severity: str
    category: str
    artifact_ids: list[str] = Field(default_factory=list)
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class FixtureArtifact(BaseModel, frozen=True):
    """A single artifact entry within a fixture pack.

    Preserves full provenance back to its IndexedArtifact source.
    `copied=False` is valid when a file is missing, unresolved, oversized,
    or binary — the copy_error or limitation explains why.
    """

    fixture_artifact_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_artifact_id: str = Field(description="ID of the source IndexedArtifact.")
    artifact_kind: str
    source_stage: str | None
    location: str
    path_role: str
    source_path: str = Field(description="Original path from the manifest.")
    fixture_relative_path: str | None = Field(
        default=None,
        description="Path relative to the fixture pack artifacts/ directory.",
    )
    content_type: str
    checksum: str | None = None
    size_bytes: int | None = None
    copied: bool = Field(description="True if the artifact file was copied into the pack.")
    copy_error: str = Field(default="", description="Reason the artifact was not copied.")
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FixturePack(BaseModel):
    """Durable fixture pack metadata.

    A fixture pack captures a structured slice of a real managed audit run.
    It preserves provenance (source run, manifest, artifact ids) so the pack
    can be loaded and used for fast slice replay in later phases without
    re-running the original audit.

    The fixture pack itself does not execute replay — that is Phase 10.
    """

    schema_version: str = "1.0"
    fixture_pack_id: str = Field(
        description="Stable, path-safe identifier. Format: {repo_id}__{run_id}__{profile}__{ts}.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str = Field(default="operations-center")
    source_repo_id: str
    source_run_id: str
    source_audit_type: str
    source_manifest_path: str = Field(
        description="Absolute path to the source artifact_manifest.json.",
    )
    source_index_summary: ArtifactIndexSummary
    harvest_profile: HarvestProfile
    selection_rationale: str = Field(
        default="",
        description="Human-readable explanation of why these artifacts were selected.",
    )
    artifacts: list[FixtureArtifact] = Field(default_factory=list)
    findings: list[FixtureFindingReference] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def artifact_count(self) -> int:
        return len(self.artifacts)

    @property
    def copied_count(self) -> int:
        return sum(1 for a in self.artifacts if a.copied)

    @property
    def metadata_only_count(self) -> int:
        return sum(1 for a in self.artifacts if not a.copied)


# ---------------------------------------------------------------------------
# Input / intermediate types (dataclasses, not serialized)
# ---------------------------------------------------------------------------

@dataclass
class CopyPolicy:
    """Controls which artifacts are copied and how much space is used."""
    max_artifact_bytes: int = 10 * 1024 * 1024  # 10 MiB
    max_total_bytes: int = 100 * 1024 * 1024    # 100 MiB
    allowed_content_types: list[str] | None = None  # None = all text/json
    include_binary_artifacts: bool = False
    include_missing_files: bool = True  # record as metadata-only when True


@dataclass
class HarvestRequest:
    """Input to the fixture harvester.

    Holds the artifact index (non-serializable) plus all selection parameters.
    """
    index: Any                                  # ManagedArtifactIndex
    harvest_profile: HarvestProfile
    artifact_ids: list[str] | None = None       # MANUAL_SELECTION or additional filter
    finding_ids: list[str] | None = None        # restrict to artifacts in these findings
    findings: list[Any] | None = None           # CalibrationFinding objects for reference
    source_stage: str | None = None             # STAGE_SLICE filter
    artifact_kind: str | None = None            # additional kind filter
    include_repo_singletons: bool = False
    include_missing_files: bool = True
    max_artifacts: int | None = None
    copy_policy: CopyPolicy = field(default_factory=CopyPolicy)
    created_by: str = "operations-center"
    selection_rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SelectedArtifact:
    """A single artifact chosen for the fixture pack, with rationale."""
    artifact: Any    # IndexedArtifact
    rationale: str


@dataclass
class FixtureSelection:
    """The result of artifact selection — ordered list with rationale."""
    selected: list[SelectedArtifact] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)  # excluded by policy

    @property
    def artifact_ids(self) -> list[str]:
        return [s.artifact.artifact_id for s in self.selected]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_\-]")


def make_fixture_pack_id(repo_id: str, run_id: str, profile: HarvestProfile) -> str:
    """Generate a stable, path-safe fixture pack id."""
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    raw = f"{repo_id}__{run_id}__{profile.value}__{ts}"
    return _SAFE_ID_RE.sub("_", raw)


__all__ = [
    "CopyPolicy",
    "FixtureArtifact",
    "FixtureFindingReference",
    "FixturePack",
    "FixtureSelection",
    "HarvestProfile",
    "HarvestRequest",
    "SelectedArtifact",
    "make_fixture_pack_id",
]
