# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for calibration report writing and loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operations_center.behavior_calibration import (
    AnalysisProfile,
    BehaviorCalibrationInput,
    analyze_artifacts,
    load_calibration_report,
    write_calibration_report,
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


class TestWriteCalibrationReport:
    def test_writes_report_file(self, tmp_path: Path, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        path = write_calibration_report(report, tmp_path)
        assert path.exists()

    def test_report_at_expected_path(self, tmp_path: Path, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        path = write_calibration_report(report, tmp_path)
        expected = tmp_path / report.repo_id / report.run_id / "summary.json"
        assert path == expected

    def test_report_is_valid_json(self, tmp_path: Path, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        path = write_calibration_report(report, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == "1.0"

    def test_creates_parent_dirs(self, tmp_path: Path, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        nested = tmp_path / "deep" / "nested" / "dir"
        path = write_calibration_report(report, nested)
        assert path.exists()

    def test_different_profiles_write_different_files(
        self, tmp_path: Path, completed_index
    ) -> None:
        r1 = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        r2 = analyze_artifacts(make_input(completed_index, AnalysisProfile.ARTIFACT_HEALTH))
        p1 = write_calibration_report(r1, tmp_path)
        p2 = write_calibration_report(r2, tmp_path)
        assert p1 != p2
        assert p1.stem == "summary"
        assert p2.stem == "artifact_health"


class TestLoadCalibrationReport:
    def test_roundtrip_write_load(self, tmp_path: Path, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        path = write_calibration_report(report, tmp_path)
        loaded = load_calibration_report(path)
        assert loaded.repo_id == report.repo_id
        assert loaded.run_id == report.run_id
        assert loaded.analysis_profile == report.analysis_profile

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_calibration_report(tmp_path / "nonexistent.json")

    def test_loaded_findings_preserved(self, tmp_path: Path, failed_index) -> None:
        report = analyze_artifacts(make_input(failed_index, AnalysisProfile.FAILURE_DIAGNOSIS))
        path = write_calibration_report(report, tmp_path)
        loaded = load_calibration_report(path)
        assert len(loaded.findings) == len(report.findings)

    def test_loaded_recommendations_preserved(self, tmp_path: Path, failed_index) -> None:
        report = analyze_artifacts(make_input(failed_index, AnalysisProfile.RECOMMENDATION))
        path = write_calibration_report(report, tmp_path)
        loaded = load_calibration_report(path)
        assert len(loaded.recommendations) == len(report.recommendations)

    def test_recommendations_require_human_review_after_roundtrip(
        self, tmp_path: Path, failed_index
    ) -> None:
        report = analyze_artifacts(make_input(failed_index, AnalysisProfile.RECOMMENDATION))
        path = write_calibration_report(report, tmp_path)
        loaded = load_calibration_report(path)
        for rec in loaded.recommendations:
            assert rec.requires_human_review is True
