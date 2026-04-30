# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Derive CalibrationRecommendations from CalibrationFindings.

Recommendations are advisory only. They are never applied automatically.
Every recommendation requires human review.

A recommendation is only produced when at least one finding supports it.
"""

from __future__ import annotations

from .models import (
    BehaviorCalibrationInput,
    CalibrationFinding,
    CalibrationRecommendation,
    FindingCategory,
    FindingSeverity,
    RecommendationPriority,
)


def _findings_by_category(
    findings: list[CalibrationFinding],
) -> dict[FindingCategory, list[CalibrationFinding]]:
    result: dict[FindingCategory, list[CalibrationFinding]] = {}
    for f in findings:
        result.setdefault(f.category, []).append(f)
    return result


def produce_recommendations(
    findings: list[CalibrationFinding],
    calibration_input: BehaviorCalibrationInput,
) -> list[CalibrationRecommendation]:
    """Derive advisory recommendations from a set of calibration findings.

    Each recommendation is anchored to one or more supporting findings.
    No recommendation is produced without at least one finding of severity
    WARNING or higher.
    """
    if not findings:
        return []

    by_cat = _findings_by_category(findings)
    recs: list[CalibrationRecommendation] = []

    repo_id = calibration_input.repo_id
    audit_type = calibration_input.audit_type

    # Failed run → investigate and re-run
    failed = by_cat.get(FindingCategory.FAILED_RUN, [])
    runtime = by_cat.get(FindingCategory.RUNTIME_FAILURE, [])
    if failed or runtime:
        supporting = [f.finding_id for f in (failed + runtime)]
        recs.append(CalibrationRecommendation(
            priority=RecommendationPriority.HIGH,
            summary="Investigate and re-run the failed audit",
            rationale=(
                f"The {repo_id}/{audit_type} audit run reported a failure. "
                "Review the manifest errors and process logs before retrying."
            ),
            affected_repo_id=repo_id,
            affected_audit_type=audit_type,
            suggested_action=(
                "Review run errors in the calibration findings. "
                "Check the dispatch stderr/stdout logs for the root cause. "
                "Re-run the audit once the issue is resolved."
            ),
            risk="medium",
            supporting_finding_ids=supporting,
        ))

    # Partial run → check interruption cause
    partial = by_cat.get(FindingCategory.PARTIAL_RUN, [])
    if partial:
        supporting = [f.finding_id for f in partial]
        recs.append(CalibrationRecommendation(
            priority=RecommendationPriority.MEDIUM,
            summary="Investigate cause of partial/interrupted run",
            rationale=(
                "The audit run was interrupted before completion. "
                "Partial artifacts may provide incomplete data for analysis."
            ),
            affected_repo_id=repo_id,
            affected_audit_type=audit_type,
            affected_artifact_ids=[
                aid for f in partial for aid in f.artifact_ids
            ],
            suggested_action=(
                "Check the dispatch result timeout setting. "
                "Review producer logs for the interruption cause. "
                "Consider increasing timeout or investigating resource constraints."
            ),
            risk="medium",
            supporting_finding_ids=supporting,
        ))

    # Missing files declared as present
    missing_files = by_cat.get(FindingCategory.MISSING_FILE, [])
    if missing_files:
        supporting = [f.finding_id for f in missing_files]
        recs.append(CalibrationRecommendation(
            priority=RecommendationPriority.HIGH,
            summary="Fix missing artifact files declared as present in manifest",
            rationale=(
                "Artifacts are declared present in the manifest but files are absent on disk. "
                "This indicates a producer-side writing failure or manifest inaccuracy."
            ),
            affected_repo_id=repo_id,
            affected_audit_type=audit_type,
            affected_artifact_ids=[
                aid for f in missing_files for aid in f.artifact_ids
            ],
            suggested_action=(
                "Review the producer's artifact write logic. "
                "Ensure finalize_success() is called only after all files are written. "
                "Check disk space and permissions in the run bucket."
            ),
            risk="high",
            supporting_finding_ids=supporting,
        ))

    # Unresolved paths
    unresolved = by_cat.get(FindingCategory.UNRESOLVED_PATH, [])
    non_external_unresolved = [
        f for f in unresolved if f.severity != FindingSeverity.INFO
    ]
    if non_external_unresolved:
        supporting = [f.finding_id for f in non_external_unresolved]
        recs.append(CalibrationRecommendation(
            priority=RecommendationPriority.LOW,
            summary="Provide repo_root to enable full artifact path resolution",
            rationale=(
                "Some artifact paths could not be resolved without a known repo root. "
                "Pass repo_root=<vf_repo_path> to build_artifact_index() for complete resolution."
            ),
            affected_repo_id=repo_id,
            affected_audit_type=audit_type,
            suggested_action=(
                "Call build_artifact_index(manifest, manifest_path, repo_root=<abs_path>) "
                "or index_dispatch_result(result, repo_root=<abs_path>)."
            ),
            risk="low",
            supporting_finding_ids=supporting,
        ))

    # Coverage gaps
    coverage = by_cat.get(FindingCategory.COVERAGE_GAP, [])
    if coverage:
        supporting = [f.finding_id for f in coverage]
        recs.append(CalibrationRecommendation(
            priority=RecommendationPriority.MEDIUM,
            summary="Investigate missing artifact categories",
            rationale=(
                "A significant proportion of declared artifacts are absent. "
                "This may indicate systematic producer failures or unconfigured artifact kinds."
            ),
            affected_repo_id=repo_id,
            affected_audit_type=audit_type,
            suggested_action=(
                "Review which artifact kinds are consistently missing across runs. "
                "Check producer stage implementation for the missing artifact kinds. "
                "Consider adjusting the audit type's scope if certain stages are optional."
            ),
            risk="medium",
            supporting_finding_ids=supporting,
        ))

    # Invalid JSON content
    invalid_json = by_cat.get(FindingCategory.INVALID_JSON, [])
    if invalid_json:
        supporting = [f.finding_id for f in invalid_json]
        recs.append(CalibrationRecommendation(
            priority=RecommendationPriority.HIGH,
            summary="Fix invalid JSON artifact content",
            rationale=(
                "One or more JSON artifacts fail to parse. This breaks downstream "
                "consumers that rely on machine-readable contract files."
            ),
            affected_repo_id=repo_id,
            affected_audit_type=audit_type,
            affected_artifact_ids=[
                aid for f in invalid_json for aid in f.artifact_ids
            ],
            suggested_action=(
                "Review the producer stage that writes these artifacts. "
                "Ensure atomic writes (write to temp file, then rename). "
                "Validate output in the producer before writing."
            ),
            risk="high",
            supporting_finding_ids=supporting,
        ))

    # Producer contract gaps
    contract_gaps = by_cat.get(FindingCategory.PRODUCER_CONTRACT_GAP, [])
    # Only recommend if there are warning-or-higher gaps
    warning_gaps = [f for f in contract_gaps if f.severity != FindingSeverity.INFO]
    if warning_gaps:
        supporting = [f.finding_id for f in warning_gaps]
        recs.append(CalibrationRecommendation(
            priority=RecommendationPriority.LOW,
            summary="Improve manifest metadata completeness",
            rationale=(
                "Some artifacts have incomplete metadata (unknown content_type, etc.). "
                "This reduces the utility of the manifest for analysis."
            ),
            affected_repo_id=repo_id,
            affected_audit_type=audit_type,
            suggested_action=(
                "Review the producer's ManagedManifestWriter.add_artifact() calls. "
                "Ensure content_type is set to a specific MIME type for each artifact."
            ),
            risk="low",
            supporting_finding_ids=supporting,
        ))

    return recs


__all__ = ["produce_recommendations"]
