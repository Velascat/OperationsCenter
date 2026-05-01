# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Artifact selection for fixture harvesting.

select_fixture_artifacts() applies profile-driven rules to a ManagedArtifactIndex
and returns an ordered FixtureSelection. All selection uses index metadata only —
no directory scanning is performed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import HarvestInputError
from .models import (
    FixtureSelection,
    HarvestProfile,
    HarvestRequest,
    SelectedArtifact,
)

if TYPE_CHECKING:
    from operations_center.artifact_index.models import ManagedArtifactIndex


# ---------------------------------------------------------------------------
# Profile selection rules
# ---------------------------------------------------------------------------

def _select_minimal_failure(
    index: "ManagedArtifactIndex",
    _request: HarvestRequest,
) -> list[SelectedArtifact]:
    """Smallest artifact set needed to inspect a failure.

    Selects: missing artifacts, partial artifacts, and any artifact
    referenced by run errors/warnings in the manifest.
    """
    result: list[SelectedArtifact] = []
    for artifact in index.artifacts:
        reasons: list[str] = []
        if artifact.status.value == "missing":
            reasons.append("missing status (failure evidence)")
        if artifact.is_partial:
            reasons.append("partial artifact (interrupted run)")
        if artifact.exists_on_disk is False:
            reasons.append("file not on disk (missing_file)")
        if reasons:
            result.append(SelectedArtifact(artifact=artifact, rationale="; ".join(reasons)))
    # Also include at least one present artifact for context when nothing else matches
    if not result:
        for artifact in index.artifacts:
            if artifact.status.value == "present":
                result.append(SelectedArtifact(artifact=artifact, rationale="only present artifact (context)"))
                break
    return result


def _select_partial_run(
    index: "ManagedArtifactIndex",
    _request: HarvestRequest,
) -> list[SelectedArtifact]:
    """Artifacts around an interrupted/partial lifecycle."""
    result: list[SelectedArtifact] = []
    for artifact in index.artifacts:
        reasons: list[str] = []
        has_partial_lim = any(lim.value == "partial_run" for lim in artifact.limitations)
        if artifact.status.value == "missing":
            reasons.append("missing (partial run)")
        if has_partial_lim:
            reasons.append("partial_run limitation")
        if artifact.is_partial:
            reasons.append("is_partial=True")
        if reasons:
            result.append(SelectedArtifact(artifact=artifact, rationale="; ".join(reasons)))
    return result


def _select_artifact_health(
    index: "ManagedArtifactIndex",
    _request: HarvestRequest,
) -> list[SelectedArtifact]:
    """Unresolved paths, missing files, and partial artifacts."""
    result: list[SelectedArtifact] = []
    for artifact in index.artifacts:
        reasons: list[str] = []
        if artifact.resolved_path is None:
            reasons.append("unresolved path")
        if artifact.exists_on_disk is False:
            reasons.append("exists_on_disk=False")
        if artifact.status.value == "missing":
            reasons.append("missing status")
        if artifact.is_partial:
            reasons.append("is_partial=True")
        if reasons:
            result.append(SelectedArtifact(artifact=artifact, rationale="; ".join(reasons)))
    return result


def _select_producer_compliance(
    index: "ManagedArtifactIndex",
    _request: HarvestRequest,
) -> list[SelectedArtifact]:
    """All artifacts — used for producer contract compliance review."""
    return [
        SelectedArtifact(artifact=a, rationale="producer compliance review")
        for a in index.artifacts
    ]


def _select_stage_slice(
    index: "ManagedArtifactIndex",
    request: HarvestRequest,
) -> list[SelectedArtifact]:
    """Artifacts for a single source_stage."""
    if not request.source_stage:
        raise HarvestInputError(
            "STAGE_SLICE profile requires source_stage to be set in HarvestRequest"
        )
    return [
        SelectedArtifact(
            artifact=a,
            rationale=f"stage slice for source_stage={request.source_stage!r}",
        )
        for a in index.artifacts
        if a.source_stage == request.source_stage
    ]


def _select_full_manifest_snapshot(
    index: "ManagedArtifactIndex",
    _request: HarvestRequest,
) -> list[SelectedArtifact]:
    """All manifest artifacts."""
    return [
        SelectedArtifact(artifact=a, rationale="full manifest snapshot")
        for a in index.artifacts
    ]


def _select_manual(
    index: "ManagedArtifactIndex",
    request: HarvestRequest,
) -> list[SelectedArtifact]:
    """Explicitly selected artifact ids."""
    if not request.artifact_ids:
        raise HarvestInputError(
            "MANUAL_SELECTION profile requires artifact_ids to be set in HarvestRequest"
        )
    id_set = set(request.artifact_ids)
    result: list[SelectedArtifact] = []
    found: set[str] = set()
    for artifact in index.artifacts:
        if artifact.artifact_id in id_set:
            result.append(SelectedArtifact(artifact=artifact, rationale="manually selected"))
            found.add(artifact.artifact_id)
    missing_ids = id_set - found
    if missing_ids:
        raise HarvestInputError(
            f"MANUAL_SELECTION: artifact ids not found in index: {sorted(missing_ids)}"
        )
    return result


_PROFILE_SELECTORS = {
    HarvestProfile.MINIMAL_FAILURE: _select_minimal_failure,
    HarvestProfile.PARTIAL_RUN: _select_partial_run,
    HarvestProfile.ARTIFACT_HEALTH: _select_artifact_health,
    HarvestProfile.PRODUCER_COMPLIANCE: _select_producer_compliance,
    HarvestProfile.STAGE_SLICE: _select_stage_slice,
    HarvestProfile.FULL_MANIFEST_SNAPSHOT: _select_full_manifest_snapshot,
    HarvestProfile.MANUAL_SELECTION: _select_manual,
}


# ---------------------------------------------------------------------------
# Post-selection filters
# ---------------------------------------------------------------------------

def _apply_finding_filter(
    selected: list[SelectedArtifact],
    finding_ids: list[str],
    findings: list[object] | None,
) -> list[SelectedArtifact]:
    """Restrict to artifacts referenced by the given finding ids."""
    if findings is None:
        return selected
    # Collect artifact_ids referenced by the target findings
    target_finding_ids = set(finding_ids)
    referenced_artifact_ids: set[str] = set()
    for f in findings:
        if getattr(f, "finding_id", None) in target_finding_ids:
            referenced_artifact_ids.update(getattr(f, "artifact_ids", []))
    if not referenced_artifact_ids:
        return selected
    return [s for s in selected if s.artifact.artifact_id in referenced_artifact_ids]


def _apply_kind_filter(
    selected: list[SelectedArtifact],
    artifact_kind: str,
) -> list[SelectedArtifact]:
    return [s for s in selected if s.artifact.artifact_kind == artifact_kind]


def _apply_singleton_filter(
    selected: list[SelectedArtifact],
    include_repo_singletons: bool,
) -> list[SelectedArtifact]:
    if include_repo_singletons:
        return selected
    return [s for s in selected if not s.artifact.is_repo_singleton]


def _apply_max_artifacts(
    selected: list[SelectedArtifact],
    max_artifacts: int,
) -> list[SelectedArtifact]:
    return selected[:max_artifacts]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_fixture_artifacts(
    index: "ManagedArtifactIndex",
    request: HarvestRequest,
) -> FixtureSelection:
    """Select artifacts from the index according to the harvest request.

    Selection is deterministic: ordering follows the artifact list order
    in the index (which reflects the manifest). No directory scanning occurs.

    Parameters
    ----------
    index:
        The ManagedArtifactIndex to select from.
    request:
        The HarvestRequest specifying profile and filters.

    Returns
    -------
    FixtureSelection
        Ordered list of selected artifacts with per-artifact rationale.

    Raises
    ------
    HarvestInputError
        If the profile requires parameters that are missing (e.g. STAGE_SLICE
        without source_stage, or MANUAL_SELECTION without artifact_ids).
    """
    profile = request.harvest_profile
    selector_fn = _PROFILE_SELECTORS.get(profile)
    if selector_fn is None:
        raise HarvestInputError(f"Unknown harvest profile: {profile!r}")

    selected = selector_fn(index, request)

    # Post-selection filters (deterministic, applied in fixed order)
    if (
        request.finding_ids
        and profile != HarvestProfile.MANUAL_SELECTION
        and request.findings
    ):
        selected = _apply_finding_filter(selected, request.finding_ids, request.findings)

    if request.artifact_kind and profile != HarvestProfile.MANUAL_SELECTION:
        selected = _apply_kind_filter(selected, request.artifact_kind)

    selected = _apply_singleton_filter(selected, request.include_repo_singletons)

    if request.max_artifacts is not None and request.max_artifacts > 0:
        selected = _apply_max_artifacts(selected, request.max_artifacts)

    # Track skipped singletons for transparency
    all_ids = {a.artifact_id for a in index.artifacts}
    selected_ids = {s.artifact.artifact_id for s in selected}
    skipped = sorted(all_ids - selected_ids)

    return FixtureSelection(selected=selected, skipped_ids=skipped)


__all__ = ["select_fixture_artifacts"]
