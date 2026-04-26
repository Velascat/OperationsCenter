"""Tests for the behavior calibration analyzer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from operations_center.artifact_index import build_artifact_index, load_artifact_manifest
from operations_center.behavior_calibration import (
    AnalysisProfile,
    BehaviorCalibrationInput,
    BehaviorCalibrationReport,
    CalibrationFinding,
    CalibrationRecommendation,
    FindingCategory,
    FindingSeverity,
    analyze_artifacts,
)
from operations_center.behavior_calibration.errors import CalibrationInputError

_RUN_ROOT = "tools/audit/report/representative/Bucket_run999"


def make_input(index, profile: AnalysisProfile, **kwargs) -> BehaviorCalibrationInput:
    return BehaviorCalibrationInput(
        repo_id=index.source.repo_id,
        run_id=index.source.run_id,
        audit_type=index.source.audit_type,
        artifact_index=index,
        analysis_profile=profile,
        **kwargs,
    )


def _base_entry(
    artifact_id: str = "videofoundry:representative:TopicSelectionStage:topic_selection",
) -> dict:
    return {
        "artifact_id": artifact_id,
        "artifact_kind": "stage_report",
        "path": f"{_RUN_ROOT}/topic_selection.json",
        "relative_path": "topic_selection.json",
        "location": "run_root",
        "path_role": "primary",
        "source_stage": "TopicSelectionStage",
        "status": "present",
        "created_at": "2026-04-26T12:00:00Z",
        "updated_at": "2026-04-26T12:00:00Z",
        "size_bytes": 512,
        "content_type": "application/json",
        "checksum": None,
        "consumer_types": ["human_review"],
        "valid_for": ["current_run_only"],
        "limitations": [],
        "description": "Topic selection output.",
        "metadata": {},
    }


def _make_manifest_payload(
    *,
    run_status: str = "completed",
    manifest_status: str = "completed",
    artifacts: list[dict] | None = None,
    excluded_paths: list[dict] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    limitations: list[str] | None = None,
    run_id: str = "run999",
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "videofoundry",
        "repo_id": "videofoundry",
        "run_id": run_id,
        "audit_type": "representative",
        "manifest_status": manifest_status,
        "run_status": run_status,
        "created_at": "2026-04-26T12:00:00Z",
        "updated_at": "2026-04-26T12:01:00Z",
        "finalized_at": "2026-04-26T12:01:00Z",
        "artifact_root": "../VideoFoundry",
        "run_root": _RUN_ROOT,
        "artifacts": artifacts if artifacts is not None else [_base_entry()],
        "excluded_paths": excluded_paths if excluded_paths is not None else [],
        "warnings": warnings if warnings is not None else [],
        "errors": errors if errors is not None else [],
        "limitations": limitations if limitations is not None else [],
        "metadata": {},
    }


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    run_root = payload["run_root"]
    bucket_dir = tmp_path / run_root
    bucket_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = bucket_dir / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_path


class TestAnalyzerInput:
    def test_raises_if_artifact_index_is_none(self) -> None:
        inp = BehaviorCalibrationInput(
            repo_id="videofoundry",
            run_id="r1",
            audit_type="representative",
            artifact_index=None,
            analysis_profile=AnalysisProfile.SUMMARY,
        )
        with pytest.raises(CalibrationInputError, match="artifact_index is required"):
            analyze_artifacts(inp)

    def test_analysis_profile_is_explicit(self, completed_index) -> None:
        inp = make_input(completed_index, AnalysisProfile.ARTIFACT_HEALTH)
        report = analyze_artifacts(inp)
        assert report.analysis_profile == AnalysisProfile.ARTIFACT_HEALTH

    def test_returns_behavior_calibration_report(self, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        assert isinstance(report, BehaviorCalibrationReport)


class TestSummaryProfile:
    def test_summary_reports_artifact_counts(self, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        s = report.artifact_index_summary
        assert s.total_artifacts == len(completed_index.artifacts)

    def test_summary_reports_by_kind(self, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        assert isinstance(report.artifact_index_summary.by_kind, dict)

    def test_summary_reports_by_location(self, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        assert isinstance(report.artifact_index_summary.by_location, dict)

    def test_summary_reports_by_status(self, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        assert isinstance(report.artifact_index_summary.by_status, dict)

    def test_summary_includes_singleton_count(self, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        assert report.artifact_index_summary.singleton_count >= 1

    def test_summary_includes_excluded_path_count(self, index_with_excluded_paths) -> None:
        report = analyze_artifacts(make_input(index_with_excluded_paths, AnalysisProfile.SUMMARY))
        assert report.artifact_index_summary.excluded_path_count == 2

    def test_summary_does_not_produce_recommendations(self, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        assert report.recommendations == []


class TestFailureDiagnosisProfile:
    def test_detects_failed_run_metadata(self, failed_index) -> None:
        report = analyze_artifacts(
            make_input(failed_index, AnalysisProfile.FAILURE_DIAGNOSIS)
        )
        categories = {f.category for f in report.findings}
        assert FindingCategory.PARTIAL_RUN in categories or FindingCategory.FAILED_RUN in categories

    def test_detects_partial_artifacts(self, failed_index) -> None:
        report = analyze_artifacts(
            make_input(failed_index, AnalysisProfile.FAILURE_DIAGNOSIS)
        )
        missing_findings = [
            f for f in report.findings
            if f.category in (FindingCategory.MISSING_ARTIFACT, FindingCategory.PARTIAL_RUN)
        ]
        assert len(missing_findings) >= 1

    def test_detects_errors_in_manifest(self, failed_index) -> None:
        report = analyze_artifacts(
            make_input(failed_index, AnalysisProfile.FAILURE_DIAGNOSIS)
        )
        runtime_or_failed = [
            f for f in report.findings
            if f.severity in (FindingSeverity.ERROR, FindingSeverity.WARNING)
        ]
        assert len(runtime_or_failed) >= 1


class TestCoverageGapsProfile:
    def test_detects_missing_artifact_categories(self, failed_index) -> None:
        report = analyze_artifacts(
            make_input(failed_index, AnalysisProfile.COVERAGE_GAPS)
        )
        categories = {f.category for f in report.findings}
        assert FindingCategory.MISSING_ARTIFACT in categories or FindingCategory.COVERAGE_GAP in categories

    def test_reports_coverage_gap_when_no_artifacts(self, empty_index) -> None:
        report = analyze_artifacts(
            make_input(empty_index, AnalysisProfile.COVERAGE_GAPS)
        )
        categories = {f.category for f in report.findings}
        assert FindingCategory.COVERAGE_GAP in categories


class TestArtifactHealthProfile:
    def test_reports_unresolved_paths(self, index_from_example_completed) -> None:
        report = analyze_artifacts(
            make_input(index_from_example_completed, AnalysisProfile.ARTIFACT_HEALTH)
        )
        assert isinstance(report, BehaviorCalibrationReport)

    def test_reports_missing_files_when_exists_on_disk_false(
        self, tmp_path: Path
    ) -> None:
        payload = _make_manifest_payload(artifacts=[_base_entry()])
        manifest_path = _write_manifest(tmp_path, payload)
        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

        report = analyze_artifacts(make_input(index, AnalysisProfile.ARTIFACT_HEALTH))
        missing_file = [f for f in report.findings if f.category == FindingCategory.MISSING_FILE]
        assert len(missing_file) >= 1


class TestProducerComplianceProfile:
    def test_producer_compliance_runs_compliance_checks(self, completed_index) -> None:
        report = analyze_artifacts(
            make_input(completed_index, AnalysisProfile.PRODUCER_COMPLIANCE)
        )
        assert isinstance(report, BehaviorCalibrationReport)

    def test_detects_unknown_content_type(self, tmp_path: Path) -> None:
        entry = {
            "artifact_id": "videofoundry:representative:Unknown:artifact",
            "artifact_kind": "unknown",
            "path": "tools/audit/report/representative/Bucket_run999/artifact.bin",
            "relative_path": "artifact.bin",
            "location": "run_root",
            "path_role": "unknown",
            "source_stage": None,
            "status": "present",
            "created_at": None, "updated_at": None,
            "size_bytes": None,
            "content_type": "unknown",
            "checksum": None,
            "consumer_types": [],
            "valid_for": [],
            "limitations": [],
            "description": "",
            "metadata": {},
        }
        payload = _make_manifest_payload(artifacts=[entry])
        manifest_path = _write_manifest(tmp_path, payload)
        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path)

        report = analyze_artifacts(make_input(index, AnalysisProfile.PRODUCER_COMPLIANCE))
        categories = {f.category for f in report.findings}
        assert FindingCategory.PRODUCER_CONTRACT_GAP in categories


class TestSingletonHandling:
    def test_singleton_limitations_preserved_in_findings(self, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        singleton_findings = [
            f for f in report.findings
            if f.category == FindingCategory.REPO_SINGLETON_WARNING
        ]
        assert len(singleton_findings) >= 1

    def test_singleton_counted_separately_in_summary(self, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        assert report.artifact_index_summary.singleton_count >= 1


class TestExcludedPathsHandling:
    def test_excluded_paths_summarized_as_findings(self, index_with_excluded_paths) -> None:
        report = analyze_artifacts(
            make_input(index_with_excluded_paths, AnalysisProfile.SUMMARY)
        )
        noise = [f for f in report.findings if f.category == FindingCategory.NOISE_EXCLUSION]
        assert len(noise) >= 1

    def test_excluded_paths_count_in_summary(self, index_with_excluded_paths) -> None:
        report = analyze_artifacts(
            make_input(index_with_excluded_paths, AnalysisProfile.SUMMARY)
        )
        assert report.artifact_index_summary.excluded_path_count == 2


class TestRecommendationProfile:
    def test_recommendations_separate_from_findings(self, failed_index) -> None:
        report = analyze_artifacts(
            make_input(failed_index, AnalysisProfile.RECOMMENDATION)
        )
        assert isinstance(report.findings, list)
        assert isinstance(report.recommendations, list)

    def test_recommendations_advisory_only(self, failed_index) -> None:
        report = analyze_artifacts(
            make_input(failed_index, AnalysisProfile.RECOMMENDATION)
        )
        for rec in report.recommendations:
            assert rec.requires_human_review is True

    def test_recommendations_produced_from_findings(self, failed_index) -> None:
        report = analyze_artifacts(
            make_input(failed_index, AnalysisProfile.RECOMMENDATION)
        )
        if report.recommendations:
            for rec in report.recommendations:
                assert len(rec.supporting_finding_ids) >= 1

    def test_no_recommendations_without_supporting_findings(self, empty_index) -> None:
        report = analyze_artifacts(
            make_input(empty_index, AnalysisProfile.RECOMMENDATION)
        )
        finding_ids = {f.finding_id for f in report.findings}
        for rec in report.recommendations:
            assert any(sid in finding_ids for sid in rec.supporting_finding_ids)

    def test_summary_profile_does_not_produce_recommendations(self, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        assert report.recommendations == []


class TestAnalyzerNonMutation:
    def test_analyzer_does_not_mutate_index(self, completed_index) -> None:
        original_len = len(completed_index.artifacts)
        analyze_artifacts(make_input(completed_index, AnalysisProfile.RECOMMENDATION))
        assert len(completed_index.artifacts) == original_len

    def test_analyzer_does_not_modify_manifests(self, completed_index) -> None:
        src_path = completed_index.source.manifest_path
        analyze_artifacts(make_input(completed_index, AnalysisProfile.RECOMMENDATION))
        assert completed_index.source.manifest_path == src_path

    def test_no_managed_repo_imports(self) -> None:
        import ast
        pkg_root = Path(__file__).resolve().parents[3] / "src" / "operations_center" / "behavior_calibration"
        for py_file in pkg_root.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("tools.audit"), (
                        f"{py_file.name} imports managed repo code: {node.module}"
                    )


class TestContentAnalysis:
    def test_content_analysis_disabled_by_default(self, completed_index) -> None:
        inp = make_input(completed_index, AnalysisProfile.ARTIFACT_HEALTH)
        assert inp.include_artifact_content is False

    def test_content_analysis_opt_in(self, tmp_path: Path) -> None:
        payload = _make_manifest_payload(artifacts=[_base_entry()])
        manifest_path = _write_manifest(tmp_path, payload)

        artifact_path = tmp_path / payload["artifacts"][0]["path"]
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps({"key": "value"}), encoding="utf-8")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

        inp = make_input(
            index, AnalysisProfile.ARTIFACT_HEALTH,
            include_artifact_content=True,
        )
        assert inp.include_artifact_content is True
        report = analyze_artifacts(inp)
        assert isinstance(report, BehaviorCalibrationReport)

    def test_invalid_json_reported_as_finding(self, tmp_path: Path) -> None:
        payload = _make_manifest_payload(artifacts=[_base_entry()])
        manifest_path = _write_manifest(tmp_path, payload)

        artifact_path = tmp_path / payload["artifacts"][0]["path"]
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("not { valid } json", encoding="utf-8")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

        inp = make_input(
            index, AnalysisProfile.ARTIFACT_HEALTH,
            include_artifact_content=True,
        )
        report = analyze_artifacts(inp)
        invalid_json = [f for f in report.findings if f.category == FindingCategory.INVALID_JSON]
        assert len(invalid_json) >= 1

    def test_content_analysis_respects_max_bytes(self, tmp_path: Path) -> None:
        payload = _make_manifest_payload(artifacts=[_base_entry()])
        manifest_path = _write_manifest(tmp_path, payload)

        big_data = {"key": "x" * 1000}
        artifact_path = tmp_path / payload["artifacts"][0]["path"]
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(big_data), encoding="utf-8")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

        inp = make_input(
            index, AnalysisProfile.ARTIFACT_HEALTH,
            include_artifact_content=True,
            max_artifact_bytes=5,
        )
        report = analyze_artifacts(inp)
        invalid = [f for f in report.findings if f.category == FindingCategory.INVALID_JSON]
        assert len(invalid) >= 1
