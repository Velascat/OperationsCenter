# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for behavior calibration input and output models."""

from __future__ import annotations

import json

import pytest

from operations_center.behavior_calibration import (
    AnalysisProfile,
    ArtifactIndexSummary,
    BehaviorCalibrationInput,
    BehaviorCalibrationReport,
    CalibrationFinding,
    CalibrationRecommendation,
    FindingCategory,
    FindingSeverity,
    RecommendationPriority,
)


def make_input(index, profile: AnalysisProfile, **kwargs) -> BehaviorCalibrationInput:
    return BehaviorCalibrationInput(
        repo_id=index.source.repo_id,
        run_id=index.source.run_id,
        audit_type=index.source.audit_type,
        artifact_index=index,
        analysis_profile=profile,
        **kwargs,
    )


class TestBehaviorCalibrationInput:
    def test_requires_artifact_index(self, completed_index) -> None:
        inp = BehaviorCalibrationInput(
            repo_id="videofoundry",
            run_id="run999",
            audit_type="representative",
            artifact_index=completed_index,
            analysis_profile=AnalysisProfile.SUMMARY,
        )
        assert inp.artifact_index is completed_index

    def test_analysis_profile_must_be_explicit(self, completed_index) -> None:
        inp = make_input(completed_index, AnalysisProfile.FAILURE_DIAGNOSIS)
        assert inp.analysis_profile == AnalysisProfile.FAILURE_DIAGNOSIS

    def test_content_opt_in_defaults_false(self, completed_index) -> None:
        inp = make_input(completed_index, AnalysisProfile.SUMMARY)
        assert inp.include_artifact_content is False

    def test_max_bytes_default(self, completed_index) -> None:
        inp = make_input(completed_index, AnalysisProfile.SUMMARY)
        assert inp.max_artifact_bytes == 10 * 1024 * 1024

    def test_selected_artifact_ids_default_none(self, completed_index) -> None:
        inp = make_input(completed_index, AnalysisProfile.SUMMARY)
        assert inp.selected_artifact_ids is None


class TestCalibrationFinding:
    def test_finding_has_auto_id(self) -> None:
        f = CalibrationFinding(
            severity=FindingSeverity.INFO,
            category=FindingCategory.NOISE_EXCLUSION,
            summary="test finding",
            source="test",
        )
        assert f.finding_id
        assert len(f.finding_id) > 0

    def test_finding_is_frozen(self) -> None:
        f = CalibrationFinding(
            severity=FindingSeverity.WARNING,
            category=FindingCategory.MISSING_ARTIFACT,
            summary="test",
            source="test",
        )
        with pytest.raises(Exception):
            f.summary = "mutated"  # type: ignore[misc]

    def test_finding_serializes_to_json(self) -> None:
        f = CalibrationFinding(
            severity=FindingSeverity.ERROR,
            category=FindingCategory.MISSING_FILE,
            summary="file missing",
            source="check_missing_files",
        )
        data = json.loads(f.model_dump_json())
        assert data["severity"] == "error"
        assert data["category"] == "missing_file"


class TestCalibrationRecommendation:
    def test_requires_human_review_is_always_true(self) -> None:
        r = CalibrationRecommendation(
            priority=RecommendationPriority.HIGH,
            summary="fix something",
            rationale="because it's broken",
            affected_repo_id="videofoundry",
            affected_audit_type="representative",
            suggested_action="do X",
        )
        assert r.requires_human_review is True

    def test_recommendation_is_frozen(self) -> None:
        r = CalibrationRecommendation(
            priority=RecommendationPriority.LOW,
            summary="fix",
            rationale="reason",
            affected_repo_id="videofoundry",
            affected_audit_type="representative",
            suggested_action="do Y",
        )
        with pytest.raises(Exception):
            r.summary = "mutated"  # type: ignore[misc]

    def test_recommendation_serializes_to_json(self) -> None:
        r = CalibrationRecommendation(
            priority=RecommendationPriority.MEDIUM,
            summary="investigate",
            rationale="missing files",
            affected_repo_id="videofoundry",
            affected_audit_type="representative",
            suggested_action="check logs",
        )
        data = json.loads(r.model_dump_json())
        assert data["requires_human_review"] is True


class TestBehaviorCalibrationReport:
    def test_report_serializes_to_json(self, completed_index) -> None:
        from operations_center.behavior_calibration import analyze_artifacts
        inp = make_input(completed_index, AnalysisProfile.SUMMARY)
        report = analyze_artifacts(inp)
        json_str = report.model_dump_json()
        data = json.loads(json_str)
        assert data["schema_version"] == "1.0"
        assert data["repo_id"] == "videofoundry"

    def test_report_has_errors_detects_error_findings(self) -> None:
        summary = ArtifactIndexSummary(
            total_artifacts=1, by_kind={}, by_location={}, by_status={},
            singleton_count=0, partial_count=0, excluded_path_count=0,
            unresolved_path_count=0, missing_file_count=0,
            machine_readable_count=0, warnings_count=0, errors_count=0,
            manifest_limitations=[],
        )
        report = BehaviorCalibrationReport(
            repo_id="videofoundry",
            run_id="r1",
            audit_type="representative",
            analysis_profile=AnalysisProfile.SUMMARY,
            artifact_index_summary=summary,
            findings=[
                CalibrationFinding(
                    severity=FindingSeverity.ERROR,
                    category=FindingCategory.FAILED_RUN,
                    summary="run failed",
                    source="test",
                )
            ],
        )
        assert report.has_errors is True

    def test_report_has_errors_false_for_info_only(self) -> None:
        summary = ArtifactIndexSummary(
            total_artifacts=1, by_kind={}, by_location={}, by_status={},
            singleton_count=0, partial_count=0, excluded_path_count=0,
            unresolved_path_count=0, missing_file_count=0,
            machine_readable_count=0, warnings_count=0, errors_count=0,
            manifest_limitations=[],
        )
        report = BehaviorCalibrationReport(
            repo_id="videofoundry",
            run_id="r1",
            audit_type="representative",
            analysis_profile=AnalysisProfile.SUMMARY,
            artifact_index_summary=summary,
            findings=[
                CalibrationFinding(
                    severity=FindingSeverity.INFO,
                    category=FindingCategory.NOISE_EXCLUSION,
                    summary="info only",
                    source="test",
                )
            ],
        )
        assert report.has_errors is False
