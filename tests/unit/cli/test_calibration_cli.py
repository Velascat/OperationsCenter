"""CLI tests for operations-center-calibration commands.

Covers analyze / tune-autonomy / report using typer.testing.CliRunner.
Manifest loading and calibration analysis are monkeypatched.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from operations_center.entrypoints.calibration.main import app

_runner = CliRunner()

_LOAD_MANIFEST_TARGET = "operations_center.entrypoints.calibration.main.load_artifact_manifest"
_BUILD_INDEX_TARGET = "operations_center.entrypoints.calibration.main.build_artifact_index"
_ANALYZE_TARGET = "operations_center.entrypoints.calibration.main.analyze_artifacts"
_WRITE_REPORT_TARGET = "operations_center.entrypoints.calibration.main.write_calibration_report"
_LOAD_REPORT_TARGET = "operations_center.entrypoints.calibration.main.load_calibration_report"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_index(repo_id: str = "videofoundry", audit_type: str = "representative") -> MagicMock:
    index = MagicMock()
    index.source.repo_id = repo_id
    index.source.audit_type = audit_type
    index.source.run_id = "run_001"
    return index


def _make_mock_report(has_errors: bool = False) -> MagicMock:
    report = MagicMock()
    report.repo_id = "videofoundry"
    report.audit_type = "representative"
    report.analysis_profile.value = "summary"
    report.has_errors = has_errors
    report.findings = []
    report.recommendations = []

    summary = MagicMock()
    summary.total_artifacts = 3
    summary.singleton_count = 0
    summary.by_status = {"present": 3}
    summary.missing_file_count = 0
    summary.unresolved_path_count = 0
    summary.excluded_path_count = 0
    report.artifact_index_summary = summary

    report.model_dump_json = MagicMock(return_value=json.dumps({
        "repo_id": "videofoundry",
        "audit_type": "representative",
        "analysis_profile": "summary",
    }))
    return report


def _make_manifest_file(tmp_path: Path) -> Path:
    p = tmp_path / "artifact_manifest.json"
    p.write_text("{}", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# cmd_analyze
# ---------------------------------------------------------------------------

class TestCmdAnalyze:
    def test_analyze_summary_profile(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        index = _make_mock_index()
        report = _make_mock_report()
        with (
            patch(_LOAD_MANIFEST_TARGET, return_value=MagicMock()),
            patch(_BUILD_INDEX_TARGET, return_value=index),
            patch(_ANALYZE_TARGET, return_value=report),
        ):
            out = _runner.invoke(app, ["analyze", "--manifest", str(mf), "--profile", "summary"])
        assert out.exit_code == 0
        assert "videofoundry" in out.output

    def test_analyze_json_output(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        index = _make_mock_index()
        report = _make_mock_report()
        with (
            patch(_LOAD_MANIFEST_TARGET, return_value=MagicMock()),
            patch(_BUILD_INDEX_TARGET, return_value=index),
            patch(_ANALYZE_TARGET, return_value=report),
        ):
            out = _runner.invoke(app, ["analyze", "--manifest", str(mf), "--json"])
        assert out.exit_code == 0

    def test_analyze_invalid_profile_exits_code_3(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        out = _runner.invoke(app, ["analyze", "--manifest", str(mf), "--profile", "not_a_profile"])
        assert out.exit_code == 3
        assert "Invalid profile" in out.output

    def test_analyze_has_errors_exits_code_1(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        index = _make_mock_index()
        report = _make_mock_report(has_errors=True)
        with (
            patch(_LOAD_MANIFEST_TARGET, return_value=MagicMock()),
            patch(_BUILD_INDEX_TARGET, return_value=index),
            patch(_ANALYZE_TARGET, return_value=report),
        ):
            out = _runner.invoke(app, ["analyze", "--manifest", str(mf)])
        assert out.exit_code == 1

    def test_analyze_writes_report_when_output_dir_given(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        index = _make_mock_index()
        report = _make_mock_report()
        written_path = tmp_path / "report.json"
        with (
            patch(_LOAD_MANIFEST_TARGET, return_value=MagicMock()),
            patch(_BUILD_INDEX_TARGET, return_value=index),
            patch(_ANALYZE_TARGET, return_value=report),
            patch(_WRITE_REPORT_TARGET, return_value=written_path) as mock_write,
        ):
            out = _runner.invoke(app, ["analyze", "--manifest", str(mf), "--output-dir", str(tmp_path)])
        assert mock_write.called

    def test_analyze_not_found_exits_code_1(self, tmp_path: Path):
        from operations_center.artifact_index import ManifestNotFoundError
        mf = _make_manifest_file(tmp_path)
        with patch(_LOAD_MANIFEST_TARGET, side_effect=ManifestNotFoundError("missing")):
            out = _runner.invoke(app, ["analyze", "--manifest", str(mf)])
        assert out.exit_code == 1


# ---------------------------------------------------------------------------
# cmd_tune_autonomy
# ---------------------------------------------------------------------------

class TestCmdTuneAutonomy:
    def test_tune_autonomy_no_recommendations(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        index = _make_mock_index()
        report = _make_mock_report()
        with (
            patch(_LOAD_MANIFEST_TARGET, return_value=MagicMock()),
            patch(_BUILD_INDEX_TARGET, return_value=index),
            patch(_ANALYZE_TARGET, return_value=report),
        ):
            out = _runner.invoke(app, ["tune-autonomy", "--manifest", str(mf)])
        assert out.exit_code == 0
        assert "No recommendations" in out.output

    def test_tune_autonomy_json_output(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        index = _make_mock_index()
        report = _make_mock_report()
        with (
            patch(_LOAD_MANIFEST_TARGET, return_value=MagicMock()),
            patch(_BUILD_INDEX_TARGET, return_value=index),
            patch(_ANALYZE_TARGET, return_value=report),
        ):
            out = _runner.invoke(app, ["tune-autonomy", "--manifest", str(mf), "--json"])
        assert out.exit_code == 0


# ---------------------------------------------------------------------------
# cmd_report
# ---------------------------------------------------------------------------

class TestCmdReport:
    def test_report_displays_summary(self, tmp_path: Path):
        report_file = tmp_path / "calibration_report.json"
        report_file.write_text("{}", encoding="utf-8")
        report = _make_mock_report()
        with patch(_LOAD_REPORT_TARGET, return_value=report):
            out = _runner.invoke(app, ["report", str(report_file)])
        assert out.exit_code == 0
        assert "videofoundry" in out.output

    def test_report_not_found_exits_code_1(self, tmp_path: Path):
        with patch(_LOAD_REPORT_TARGET, side_effect=FileNotFoundError("missing")):
            out = _runner.invoke(app, ["report", "/nonexistent/report.json"])
        assert out.exit_code == 1
        assert "Not found" in out.output

    def test_report_json_output(self, tmp_path: Path):
        report_file = tmp_path / "calibration_report.json"
        report_file.write_text("{}", encoding="utf-8")
        report = _make_mock_report()
        with patch(_LOAD_REPORT_TARGET, return_value=report):
            out = _runner.invoke(app, ["report", str(report_file), "--json"])
        assert out.exit_code == 0
