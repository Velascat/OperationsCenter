# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Calibration rules — pure functions that produce CalibrationFindings.

Each rule function accepts a ManagedArtifactIndex and returns a list of
CalibrationFindings. Rules are deterministic and do not mutate the index.

Rules are grouped by the analysis profiles that use them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from operations_center.audit_contracts.vocabulary import ArtifactStatus, Location, RunStatus

from .models import CalibrationFinding, FindingCategory, FindingSeverity

if TYPE_CHECKING:
    from operations_center.artifact_index.models import ManagedArtifactIndex


def check_run_status(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    """Report findings based on the manifest's run_status and manifest_status."""
    findings = []
    rs = index.run_status
    ms = index.manifest_status

    if rs == RunStatus.FAILED:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.ERROR,
            category=FindingCategory.FAILED_RUN,
            summary=f"Audit run reported failed status: run_status={rs.value}",
            detail=(
                f"The manifest records run_status={rs.value} and "
                f"manifest_status={ms.value}. Investigate errors in the report."
            ),
            source="check_run_status",
        ))
    elif rs == RunStatus.INTERRUPTED:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.WARNING,
            category=FindingCategory.PARTIAL_RUN,
            summary=f"Audit run was interrupted: run_status={rs.value}",
            detail=(
                "The run did not complete normally. Artifacts produced before "
                "interruption may be present; downstream artifacts will be missing."
            ),
            source="check_run_status",
        ))
    elif rs not in (RunStatus.COMPLETED,):
        findings.append(CalibrationFinding(
            severity=FindingSeverity.WARNING,
            category=FindingCategory.UNKNOWN,
            summary=f"Unexpected run_status value: {rs.value}",
            source="check_run_status",
        ))

    if index.errors:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.ERROR,
            category=FindingCategory.RUNTIME_FAILURE,
            summary=f"Manifest records {len(index.errors)} error(s)",
            detail="\n".join(index.errors[:5]),
            source="check_run_status",
        ))

    if index.warnings:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.WARNING,
            category=FindingCategory.UNKNOWN,
            summary=f"Manifest records {len(index.warnings)} warning(s)",
            detail="\n".join(index.warnings[:5]),
            source="check_run_status",
        ))

    return findings


def check_partial_artifacts(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    """Report findings for artifacts with MISSING/EXPECTED status or partial_run limitation."""
    findings = []

    missing = [
        a for a in index.artifacts
        if a.status == ArtifactStatus.MISSING
    ]
    if missing:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.WARNING,
            category=FindingCategory.MISSING_ARTIFACT,
            summary=f"{len(missing)} artifact(s) have status=missing",
            detail=(
                "These artifacts were declared in the manifest but the producer "
                "did not write them. This often indicates a partial run or early failure."
            ),
            artifact_ids=[a.artifact_id for a in missing],
            source="check_partial_artifacts",
        ))

    partial = [a for a in index.artifacts if a.is_partial]
    if partial:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.WARNING,
            category=FindingCategory.PARTIAL_RUN,
            summary=f"{len(partial)} artifact(s) carry the partial_run limitation",
            artifact_ids=[a.artifact_id for a in partial],
            source="check_partial_artifacts",
        ))

    return findings


def check_unresolved_paths(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    """Report findings for artifacts whose path could not be resolved."""
    unresolved = [a for a in index.artifacts if a.resolved_path is None]
    if not unresolved:
        return []

    external = [a for a in unresolved if a.location == Location.EXTERNAL_OR_UNKNOWN]
    non_external = [a for a in unresolved if a.location != Location.EXTERNAL_OR_UNKNOWN]

    findings = []
    if non_external:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.WARNING,
            category=FindingCategory.UNRESOLVED_PATH,
            summary=f"{len(non_external)} artifact(s) have unresolvable relative paths",
            detail=(
                "Paths could not be resolved without a known repo root. "
                "Pass repo_root to build_artifact_index() for full path resolution."
            ),
            artifact_ids=[a.artifact_id for a in non_external],
            source="check_unresolved_paths",
        ))
    if external:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.INFO,
            category=FindingCategory.UNRESOLVED_PATH,
            summary=f"{len(external)} artifact(s) have external_or_unknown location",
            detail="These artifacts are declared as external — paths are not resolved by policy.",
            artifact_ids=[a.artifact_id for a in external],
            source="check_unresolved_paths",
        ))

    return findings


def check_missing_files(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    """Report findings for artifacts whose path resolved but the file is absent on disk."""
    absent = [
        a for a in index.artifacts
        if a.exists_on_disk is False and a.resolved_path is not None
    ]
    if not absent:
        return []

    return [CalibrationFinding(
        severity=FindingSeverity.ERROR,
        category=FindingCategory.MISSING_FILE,
        summary=f"{len(absent)} artifact(s) are declared present but missing from disk",
        detail=(
            "The manifest declares these artifacts as present, but the files "
            "do not exist at the resolved path. This may indicate incomplete "
            "artifact writing or a path encoding issue."
        ),
        artifact_ids=[a.artifact_id for a in absent],
        source="check_missing_files",
    )]


def check_singleton_limitations(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    """Report findings related to repo_singleton artifacts and their limitations."""
    singletons = index.singleton_artifacts
    if not singletons:
        return []

    findings = []
    from operations_center.audit_contracts.vocabulary import Limitation
    overwritten = [
        a for a in singletons
        if Limitation.REPO_SINGLETON_OVERWRITTEN in a.limitations
    ]
    if overwritten:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.INFO,
            category=FindingCategory.REPO_SINGLETON_WARNING,
            summary=(
                f"{len(overwritten)} repo_singleton artifact(s) carry the "
                "repo_singleton_overwritten limitation"
            ),
            detail=(
                "Repo singleton artifacts are overwritten in-place on each run. "
                "They reflect the most recent run, not necessarily this audit run."
            ),
            artifact_ids=[a.artifact_id for a in overwritten],
            source="check_singleton_limitations",
        ))

    return findings


def check_excluded_paths(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    """Summarize excluded infrastructure noise paths."""
    if not index.excluded_paths:
        return []

    return [CalibrationFinding(
        severity=FindingSeverity.INFO,
        category=FindingCategory.NOISE_EXCLUSION,
        summary=f"{len(index.excluded_paths)} infrastructure path(s) excluded from artifacts",
        detail=(
            "Excluded paths are infrastructure noise (coverage.ini, .coverage.*, "
            "sitecustomize.py). They are preserved in the index but never treated as artifacts."
        ),
        source="check_excluded_paths",
        metadata={"excluded_path_count": len(index.excluded_paths)},
    )]


def check_producer_compliance(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    """Check manifest/index quality for producer contract gaps."""
    findings = []

    # Artifacts with empty artifact_id, description, or unknown content_type
    unknown_content = [
        a for a in index.artifacts
        if a.content_type in ("unknown", "")
    ]
    if unknown_content:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.WARNING,
            category=FindingCategory.PRODUCER_CONTRACT_GAP,
            summary=f"{len(unknown_content)} artifact(s) have unknown content_type",
            artifact_ids=[a.artifact_id for a in unknown_content],
            source="check_producer_compliance",
        ))

    no_description = [
        a for a in index.artifacts
        if not a.description
    ]
    if no_description:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.INFO,
            category=FindingCategory.PRODUCER_CONTRACT_GAP,
            summary=f"{len(no_description)} artifact(s) have no description",
            artifact_ids=[a.artifact_id for a in no_description],
            source="check_producer_compliance",
        ))

    no_consumer = [
        a for a in index.artifacts
        if not a.consumer_types
    ]
    if no_consumer:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.INFO,
            category=FindingCategory.PRODUCER_CONTRACT_GAP,
            summary=f"{len(no_consumer)} artifact(s) declare no consumer_types",
            artifact_ids=[a.artifact_id for a in no_consumer],
            source="check_producer_compliance",
        ))

    no_valid_for = [
        a for a in index.artifacts
        if not a.valid_for
    ]
    if no_valid_for:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.INFO,
            category=FindingCategory.PRODUCER_CONTRACT_GAP,
            summary=f"{len(no_valid_for)} artifact(s) declare no valid_for scope",
            artifact_ids=[a.artifact_id for a in no_valid_for],
            source="check_producer_compliance",
        ))

    return findings


def check_coverage_gaps(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    """Identify categories of artifacts that are entirely absent or all-missing."""
    findings = []

    present = [a for a in index.artifacts if a.status == ArtifactStatus.MISSING]
    total = len(index.artifacts)

    if total == 0:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.ERROR,
            category=FindingCategory.COVERAGE_GAP,
            summary="No artifacts declared in manifest",
            detail=(
                "The manifest contains no artifact entries. "
                "This may indicate the run failed before any artifact was produced, "
                "or the producer did not finalize the manifest."
            ),
            source="check_coverage_gaps",
        ))
        return findings

    missing_ratio = len(present) / total
    if missing_ratio >= 0.5 and len(present) >= 2:
        findings.append(CalibrationFinding(
            severity=FindingSeverity.WARNING,
            category=FindingCategory.COVERAGE_GAP,
            summary=(
                f"{len(present)}/{total} declared artifacts are missing "
                f"({missing_ratio:.0%} coverage gap)"
            ),
            detail=(
                "A significant portion of declared artifacts were not produced. "
                "This may indicate an interrupted run or systematic producer failure."
            ),
            artifact_ids=[a.artifact_id for a in present],
            source="check_coverage_gaps",
        ))

    # Check if entire kind categories are all-missing
    from collections import defaultdict
    by_kind: dict[str, list] = defaultdict(list)
    for a in index.artifacts:
        by_kind[a.artifact_kind].append(a)

    for kind, entries in by_kind.items():
        if entries and all(a.status == ArtifactStatus.MISSING for a in entries):
            findings.append(CalibrationFinding(
                severity=FindingSeverity.WARNING,
                category=FindingCategory.COVERAGE_GAP,
                summary=f"All {len(entries)} artifact(s) of kind '{kind}' are missing",
                artifact_ids=[a.artifact_id for a in entries],
                source="check_coverage_gaps",
            ))

    return findings


def check_content(
    index: "ManagedArtifactIndex",
    selected_ids: list[str] | None,
    max_bytes: int,
) -> list[CalibrationFinding]:
    """Optionally read artifact contents and check readability.

    Uses Phase 7 retrieval helpers only. Never mutates files.
    """
    from operations_center.artifact_index.retrieval import (
        read_json_artifact,
        read_text_artifact,
    )
    from operations_center.artifact_index.errors import (
        ArtifactPathUnresolvableError,
    )

    findings = []

    candidates = index.artifacts
    if selected_ids is not None:
        candidates = [a for a in candidates if a.artifact_id in selected_ids]

    # Only attempt to read artifacts that are present on disk and resolvable
    readable = [
        a for a in candidates
        if a.resolved_path is not None and a.exists_on_disk is True
    ]

    for artifact in readable:
        if artifact.is_machine_readable and "json" in artifact.content_type:
            try:
                read_json_artifact(index, artifact.artifact_id, max_bytes=max_bytes)
            except Exception as exc:
                findings.append(CalibrationFinding(
                    severity=FindingSeverity.ERROR,
                    category=FindingCategory.INVALID_JSON,
                    summary=f"Artifact '{artifact.artifact_id}' failed JSON parse",
                    detail=str(exc),
                    artifact_ids=[artifact.artifact_id],
                    source="check_content",
                    confidence="high",
                ))
        elif artifact.content_type.startswith("text/"):
            try:
                read_text_artifact(index, artifact.artifact_id, max_bytes=max_bytes)
            except ArtifactPathUnresolvableError:
                pass  # already caught by check_unresolved_paths
            except Exception as exc:
                findings.append(CalibrationFinding(
                    severity=FindingSeverity.WARNING,
                    category=FindingCategory.MISSING_FILE,
                    summary=f"Artifact '{artifact.artifact_id}' could not be read as text",
                    detail=str(exc),
                    artifact_ids=[artifact.artifact_id],
                    source="check_content",
                    confidence="medium",
                ))

    return findings


__all__ = [
    "check_content",
    "check_coverage_gaps",
    "check_excluded_paths",
    "check_missing_files",
    "check_partial_artifacts",
    "check_producer_compliance",
    "check_run_status",
    "check_singleton_limitations",
    "check_unresolved_paths",
]
