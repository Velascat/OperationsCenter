# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""OperationsCenter-side artifact index models.

These are derived/internal models built from a ManagedArtifactManifest.
They are not contract types — the manifest remains the source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from operations_center.audit_contracts.artifact_manifest import ExcludedPath
from operations_center.audit_contracts.vocabulary import (
    ArtifactStatus,
    ConsumerType,
    Limitation,
    Location,
    ManifestStatus,
    PathRole,
    RunStatus,
    ValidFor,
)


@dataclass(frozen=True)
class IndexedArtifact:
    """A single artifact entry in the index.

    Preserves all manifest fields and adds OperationsCenter-derived fields.
    Derived fields (exists_on_disk, is_repo_singleton, is_partial,
    is_machine_readable) do not mutate the source manifest.
    """

    # --- manifest-level provenance ---
    artifact_id: str
    repo_id: str
    producer: str
    run_id: str
    audit_type: str
    manifest_path: str  # absolute path to the manifest file

    # --- manifest entry fields (preserved verbatim) ---
    artifact_kind: str
    location: Location
    path_role: PathRole
    source_stage: str | None
    status: ArtifactStatus
    path: str
    relative_path: str | None
    content_type: str
    size_bytes: int | None
    checksum: str | None
    consumer_types: list[ConsumerType]
    valid_for: list[ValidFor]
    limitations: list[Limitation]
    description: str
    metadata: dict[str, Any]

    # --- OpsCenter-derived fields ---
    resolved_path: Path | None
    """Absolute Path if resolvable; None if the path cannot be safely resolved."""

    exists_on_disk: bool | None
    """True/False if existence was verified; None if not checked."""

    is_repo_singleton: bool
    is_partial: bool
    is_machine_readable: bool


@dataclass(frozen=True)
class ArtifactIndexSource:
    """Provenance record for where a ManagedArtifactIndex was built from."""

    manifest_path: str
    repo_id: str
    run_id: str
    audit_type: str
    producer: str


@dataclass
class ManagedArtifactIndex:
    """In-memory index built from a single artifact_manifest.json.

    The manifest is the source of truth. The index is a derived view.
    """

    source: ArtifactIndexSource
    manifest_status: ManifestStatus
    run_status: RunStatus
    artifact_root: str | None
    run_root: str | None
    artifacts: list[IndexedArtifact]
    excluded_paths: list[ExcludedPath]
    warnings: list[str]
    errors: list[str]
    limitations: list[Limitation]
    metadata: dict[str, Any] = field(default_factory=dict)

    # --- convenience properties ---

    @property
    def singleton_artifacts(self) -> list[IndexedArtifact]:
        return [a for a in self.artifacts if a.is_repo_singleton]

    @property
    def run_scoped_artifacts(self) -> list[IndexedArtifact]:
        return [a for a in self.artifacts if not a.is_repo_singleton]

    def get_by_id(self, artifact_id: str) -> IndexedArtifact | None:
        return next((a for a in self.artifacts if a.artifact_id == artifact_id), None)


@dataclass(frozen=True)
class ArtifactQuery:
    """Filter criteria for query_artifacts().

    All fields are optional. An unset field (None) is not applied as a filter.
    A None ArtifactQuery is equivalent to querying with all fields unset.

    consumer_type, valid_for, and limitation are membership tests:
        the artifact must include that value in its list field.
    """

    repo_id: str | None = None
    run_id: str | None = None
    audit_type: str | None = None
    artifact_kind: str | None = None
    location: Location | None = None
    path_role: PathRole | None = None
    source_stage: str | None = None
    status: ArtifactStatus | None = None
    consumer_type: ConsumerType | None = None
    valid_for: ValidFor | None = None
    limitation: Limitation | None = None
    content_type: str | None = None
    exists_on_disk: bool | None = None
    is_repo_singleton: bool | None = None
    is_partial: bool | None = None


__all__ = [
    "ArtifactIndexSource",
    "ArtifactQuery",
    "IndexedArtifact",
    "ManagedArtifactIndex",
]
