# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""analyze_artifacts() — main calibration analysis entry point.

Routes a BehaviorCalibrationInput to the appropriate set of rules based
on the analysis profile and produces a BehaviorCalibrationReport.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from .errors import CalibrationInputError
from .models import (
    AnalysisProfile,
    ArtifactIndexSummary,
    BehaviorCalibrationInput,
    BehaviorCalibrationReport,
    CalibrationFinding,
)
from .recommendations import produce_recommendations
from .rules import (
    check_content,
    check_coverage_gaps,
    check_excluded_paths,
    check_missing_files,
    check_partial_artifacts,
    check_producer_compliance,
    check_run_status,
    check_singleton_limitations,
    check_unresolved_paths,
)

if TYPE_CHECKING:
    from operations_center.artifact_index.models import ManagedArtifactIndex


def _build_index_summary(index: "ManagedArtifactIndex") -> ArtifactIndexSummary:
    """Compute summary statistics from the index."""
    by_kind: dict[str, int] = defaultdict(int)
    by_location: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    partial_count = 0
    unresolved_count = 0
    missing_file_count = 0
    machine_readable_count = 0

    for artifact in index.artifacts:
        by_kind[artifact.artifact_kind] += 1
        by_location[artifact.location.value] += 1
        by_status[artifact.status.value] += 1
        if artifact.is_partial:
            partial_count += 1
        if artifact.resolved_path is None:
            unresolved_count += 1
        if artifact.exists_on_disk is False:
            missing_file_count += 1
        if artifact.is_machine_readable:
            machine_readable_count += 1

    return ArtifactIndexSummary(
        total_artifacts=len(index.artifacts),
        by_kind=dict(by_kind),
        by_location=dict(by_location),
        by_status=dict(by_status),
        singleton_count=len(index.singleton_artifacts),
        partial_count=partial_count,
        excluded_path_count=len(index.excluded_paths),
        unresolved_path_count=unresolved_count,
        missing_file_count=missing_file_count,
        machine_readable_count=machine_readable_count,
        warnings_count=len(index.warnings),
        errors_count=len(index.errors),
        manifest_limitations=[lim.value for lim in index.limitations],
    )


def _run_summary_rules(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    return (
        check_run_status(index)
        + check_excluded_paths(index)
        + check_singleton_limitations(index)
    )


def _run_failure_diagnosis_rules(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    return (
        check_run_status(index)
        + check_partial_artifacts(index)
        + check_missing_files(index)
    )


def _run_coverage_gap_rules(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    return (
        check_coverage_gaps(index)
        + check_partial_artifacts(index)
    )


def _run_artifact_health_rules(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    return (
        check_unresolved_paths(index)
        + check_missing_files(index)
        + check_partial_artifacts(index)
    )


def _run_producer_compliance_rules(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    return check_producer_compliance(index)


def _run_all_rules(index: "ManagedArtifactIndex") -> list[CalibrationFinding]:
    """Run all rule sets for the recommendation profile."""
    return (
        check_run_status(index)
        + check_partial_artifacts(index)
        + check_unresolved_paths(index)
        + check_missing_files(index)
        + check_singleton_limitations(index)
        + check_excluded_paths(index)
        + check_producer_compliance(index)
        + check_coverage_gaps(index)
    )


_PROFILE_RULES = {
    AnalysisProfile.SUMMARY: _run_summary_rules,
    AnalysisProfile.FAILURE_DIAGNOSIS: _run_failure_diagnosis_rules,
    AnalysisProfile.COVERAGE_GAPS: _run_coverage_gap_rules,
    AnalysisProfile.ARTIFACT_HEALTH: _run_artifact_health_rules,
    AnalysisProfile.PRODUCER_COMPLIANCE: _run_producer_compliance_rules,
    AnalysisProfile.RECOMMENDATION: _run_all_rules,
}


def analyze_artifacts(
    calibration_input: BehaviorCalibrationInput,
) -> BehaviorCalibrationReport:
    """Run calibration analysis and return a structured report.

    The analysis is read-only. The artifact index is never mutated.
    Artifact content is only read when calibration_input.include_artifact_content is True.

    Parameters
    ----------
    calibration_input:
        A BehaviorCalibrationInput with artifact_index and analysis_profile set.

    Returns
    -------
    BehaviorCalibrationReport
        Always returned — failures are recorded as findings, not exceptions.

    Raises
    ------
    CalibrationInputError
        If artifact_index is None or analysis_profile is not a valid AnalysisProfile.
    """
    if calibration_input.artifact_index is None:
        raise CalibrationInputError("artifact_index is required for calibration analysis")

    profile = calibration_input.analysis_profile
    rule_fn = _PROFILE_RULES.get(profile)
    if rule_fn is None:
        raise CalibrationInputError(f"unsupported analysis_profile: {profile!r}")

    index = calibration_input.artifact_index
    summary = _build_index_summary(index)

    findings: list[CalibrationFinding] = rule_fn(index)

    # Optional content analysis — uses Phase 7 helpers, opt-in only
    if calibration_input.include_artifact_content:
        content_findings = check_content(
            index,
            calibration_input.selected_artifact_ids,
            calibration_input.max_artifact_bytes,
        )
        findings = findings + content_findings

    # Recommendations only for the RECOMMENDATION profile (and derived from findings)
    recommendations = []
    if profile == AnalysisProfile.RECOMMENDATION:
        recommendations = produce_recommendations(findings, calibration_input)

    limitations = [lim.value for lim in index.limitations]

    return BehaviorCalibrationReport(
        repo_id=calibration_input.repo_id,
        run_id=calibration_input.run_id,
        audit_type=calibration_input.audit_type,
        analysis_profile=profile,
        artifact_index_summary=summary,
        findings=findings,
        recommendations=recommendations,
        limitations=limitations,
        metadata=dict(calibration_input.metadata),
    )


__all__ = ["analyze_artifacts"]
