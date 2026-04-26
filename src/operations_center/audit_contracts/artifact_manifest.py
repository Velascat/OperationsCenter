"""
artifact_manifest.py — generic managed-repo artifact manifest contract model.

artifact_manifest.json is produced by the managed repo (VideoFoundry),
defined by OperationsCenter, and later read by OpsCenter indexing.

Design rules:
- The manifest supports incremental writing: it is valid during initializing
  and running states, not only after finalization.
- repo_singleton artifacts have run_id=None (they are not scoped to a run).
- Infrastructure noise (coverage.ini, .coverage.*, sitecustomize.py) is
  recorded in excluded_paths, NOT in artifacts.
- artifact_kind, source_stage, consumer_types, valid_for, and limitations
  use producer profile vocabulary (e.g. VideoFoundryArtifactKind).
  The generic model accepts str for these to avoid baking in VF values.

Lifecycle transitions:
    initializing → running → completed
    initializing → running → failed
    initializing → running → partial (interrupted)

Fields required during initializing/running (subset):
    schema_version, contract_name, producer, repo_id, run_id, audit_type,
    manifest_status, run_status, created_at, updated_at, artifact_root, run_root

Fields required only on finalization (additions):
    finalized_at (completed/failed), artifacts (fully populated)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .vocabulary import (
    ArtifactStatus,
    ConsumerType,
    Limitation,
    Location,
    ManifestStatus,
    PathRole,
    RunStatus,
    ValidFor,
)

_CONTRACT_NAME = "managed-repo-audit"
_SCHEMA_VERSION = "1.0"


class ExcludedPath(BaseModel):
    """A path excluded from the artifact manifest because it is infrastructure noise.

    Examples: coverage.ini, .coverage.*, sitecustomize.py, __pycache__/
    """
    path: str = Field(description="Path as it appears in the bucket.")
    reason: str = Field(description="Why this path is excluded.")
    pattern: str | None = Field(
        default=None,
        description="Glob pattern that matched, if pattern-based exclusion was used.",
    )

    model_config = {"frozen": True}


class ManagedArtifactEntry(BaseModel):
    """A single artifact entry in the artifact manifest.

    Generic fields apply to any managed repo.
    artifact_kind and source_stage accept str so producer profiles
    can use their own vocabulary without constraining the generic model.
    """

    artifact_id: str = Field(
        description=(
            "Stable, unique identifier for this artifact within the run. "
            "Suggested format: '{producer}:{audit_type}:{source_stage}:{filename_slug}'. "
            "Must be stable across manifest rewrites during the same run."
        ),
    )
    artifact_kind: str = Field(
        description=(
            "Producer-vocabulary artifact kind (e.g. VideoFoundryArtifactKind values). "
            "Use 'unknown' when the kind cannot be determined."
        ),
    )
    path: str = Field(
        description="Path as recorded by the producer. May be absolute or relative.",
    )
    relative_path: str | None = Field(
        default=None,
        description=(
            "Path relative to run_root or artifact_root, where applicable. "
            "None for repo_singleton artifacts where no single root applies."
        ),
    )
    location: Location = Field(
        description="Path-layout class (run_root, artifacts_subdir, repo_singleton, etc.).",
    )
    path_role: PathRole = Field(
        default=PathRole.UNKNOWN,
        description="Semantic role of this artifact path.",
    )
    source_stage: str | None = Field(
        default=None,
        description=(
            "Producer stage that created this artifact. "
            "Use producer profile vocabulary (e.g. VideoFoundrySourceStage). "
            "None for lifecycle or unknown sources."
        ),
    )
    status: ArtifactStatus = Field(
        default=ArtifactStatus.PRESENT,
        description="Whether the artifact is present, missing, or expected.",
    )
    created_at: datetime | None = Field(
        default=None,
        description="ISO 8601 UTC timestamp when the artifact was first written.",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="ISO 8601 UTC timestamp of the most recent write.",
    )
    size_bytes: int | None = Field(
        default=None,
        description="File size in bytes at last observation. None if not yet measured.",
    )
    content_type: str = Field(
        default="unknown",
        description="MIME-like content type (e.g. 'application/json', 'text/plain').",
    )
    checksum: str | None = Field(
        default=None,
        description="Content checksum in '{algorithm}:{hex}' format (e.g. 'sha256:abc…').",
    )
    consumer_types: list[ConsumerType] = Field(
        default_factory=list,
        description="Who or what is expected to consume this artifact.",
    )
    valid_for: list[ValidFor] = Field(
        default_factory=list,
        description="Temporal/contextual scope for which this artifact is meaningful.",
    )
    limitations: list[Limitation] = Field(
        default_factory=list,
        description="Known caveats on this artifact's completeness or reliability.",
    )
    description: str = Field(
        default="",
        description="Human-readable description of what this artifact contains.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Producer-specific metadata not covered by generic fields.",
    )

    model_config = {"frozen": False}

    @property
    def is_repo_singleton(self) -> bool:
        return self.location == Location.REPO_SINGLETON

    @property
    def is_present(self) -> bool:
        return self.status == ArtifactStatus.PRESENT


class ManagedArtifactManifest(BaseModel):
    """Schema for artifact_manifest.json as defined by the managed-repo audit contract.

    Produced by VideoFoundry (Phase 5), defined by OperationsCenter (Phase 2),
    read by OpsCenter indexing (Phase 7).

    The manifest is written incrementally. It is valid during initializing
    and running states. OpsCenter must tolerate a partial manifest.

    Repo-singleton rule:
        Artifacts with location=REPO_SINGLETON have run_id=None in their
        metadata and valid_for=[latest_snapshot]. The manifest run_id still
        identifies the run that last updated the singleton reference.
    """

    schema_version: str = Field(
        default=_SCHEMA_VERSION,
        description="Contract schema version.",
    )
    contract_name: str = Field(
        default=_CONTRACT_NAME,
        description="Identifies this as a managed-repo-audit contract file.",
    )
    producer: str = Field(
        description="Managed repo identifier (e.g. 'videofoundry').",
    )
    repo_id: str = Field(
        description="Stable repo identifier matching the managed repo config.",
    )
    run_id: str = Field(
        description="UUID hex generated by OperationsCenter before invocation.",
    )
    audit_type: str = Field(
        description="Audit type identifier (e.g. 'representative').",
    )
    manifest_status: ManifestStatus = Field(
        description="Current lifecycle status of the manifest itself.",
    )
    run_status: RunStatus = Field(
        description="Current lifecycle status of the audit run.",
    )
    created_at: datetime = Field(
        description="ISO 8601 UTC timestamp when the manifest was first created.",
    )
    updated_at: datetime = Field(
        description="ISO 8601 UTC timestamp of the most recent manifest update.",
    )
    finalized_at: datetime | None = Field(
        default=None,
        description=(
            "ISO 8601 UTC timestamp when the manifest reached a terminal state. "
            "None during initializing/running. Required on completed/failed/partial."
        ),
    )
    artifact_root: str | None = Field(
        default=None,
        description=(
            "Root directory for resolving artifact paths in this manifest. "
            "Typically the VideoFoundry repo root. "
            "relative_path in each entry is relative to this root where applicable."
        ),
    )
    run_root: str | None = Field(
        default=None,
        description=(
            "The per-run bucket directory (relative to repo root). "
            "Example: tools/audit/report/representative/Connective_Contours_..._{run_id}"
        ),
    )
    artifacts: list[ManagedArtifactEntry] = Field(
        default_factory=list,
        description=(
            "Artifact entries. Populated incrementally during the run. "
            "May be empty or partial during initializing/running states."
        ),
    )
    excluded_paths: list[ExcludedPath] = Field(
        default_factory=list,
        description=(
            "Infrastructure noise paths excluded from artifact tracking. "
            "Examples: coverage.ini, .coverage.*, sitecustomize.py."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings encountered during the run or manifest writing.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Errors encountered during artifact production.",
    )
    limitations: list[Limitation] = Field(
        default_factory=list,
        description="Manifest-level limitations (e.g. partial_run, path_layout_non_uniform).",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Producer-specific metadata not covered by generic fields.",
    )

    model_config = {"frozen": False}

    @property
    def is_terminal(self) -> bool:
        return self.manifest_status in (
            ManifestStatus.COMPLETED,
            ManifestStatus.FAILED,
            ManifestStatus.PARTIAL,
        )

    @property
    def singleton_artifacts(self) -> list[ManagedArtifactEntry]:
        return [a for a in self.artifacts if a.is_repo_singleton]

    @property
    def run_scoped_artifacts(self) -> list[ManagedArtifactEntry]:
        return [a for a in self.artifacts if not a.is_repo_singleton]

    def artifact_by_id(self, artifact_id: str) -> ManagedArtifactEntry | None:
        return next((a for a in self.artifacts if a.artifact_id == artifact_id), None)


__all__ = [
    "ExcludedPath",
    "ManagedArtifactEntry",
    "ManagedArtifactManifest",
]
