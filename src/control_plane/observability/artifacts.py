"""
observability/artifacts.py — Artifact classification and indexing.

Primary artifacts represent the main output of a run (diffs, patches,
validation reports). Supplemental artifacts carry diagnostic or reference
data (logs, goal files, PR URLs, branch refs).

The distinction matters because downstream consumers may want to surface only
primary artifacts in summaries while retaining supplemental artifacts for
debugging.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from control_plane.contracts.enums import ArtifactType
from control_plane.contracts.execution import ExecutionArtifact


_PRIMARY_TYPES: frozenset[ArtifactType] = frozenset({
    ArtifactType.DIFF,
    ArtifactType.PATCH,
    ArtifactType.VALIDATION_REPORT,
})


class ArtifactIndex(BaseModel):
    """Classified index of artifacts produced during a run.

    primary_artifacts   — the main outputs (diff, patch, validation report)
    supplemental_artifacts — diagnostic/reference artifacts (logs, goal files, etc.)
    artifact_counts     — per-type counts across all artifacts
    artifact_types_present — sorted list of artifact type values present in this run
    """

    primary_artifacts: list[ExecutionArtifact] = Field(default_factory=list)
    supplemental_artifacts: list[ExecutionArtifact] = Field(default_factory=list)
    artifact_counts: dict[str, int] = Field(default_factory=dict)
    artifact_types_present: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class ArtifactNormalizer:
    """Classifies and indexes a list of ExecutionArtifacts."""

    @classmethod
    def index(cls, artifacts: list[ExecutionArtifact]) -> ArtifactIndex:
        primary = [a for a in artifacts if a.artifact_type in _PRIMARY_TYPES]
        supplemental = [a for a in artifacts if a.artifact_type not in _PRIMARY_TYPES]

        counts: dict[str, int] = {}
        for a in artifacts:
            key = a.artifact_type.value
            counts[key] = counts.get(key, 0) + 1

        types_present = sorted(counts.keys())

        return ArtifactIndex(
            primary_artifacts=primary,
            supplemental_artifacts=supplemental,
            artifact_counts=counts,
            artifact_types_present=types_present,
        )

    @classmethod
    def is_primary(cls, artifact: ExecutionArtifact) -> bool:
        return artifact.artifact_type in _PRIMARY_TYPES
