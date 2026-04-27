"""CLI tests for operations-center-regression commands.

Covers run / inspect / list using typer.testing.CliRunner.
Suite execution is always monkeypatched — no real replay is invoked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from operations_center.entrypoints.regression.main import app
from operations_center.mini_regression.models import (
    MiniRegressionEntryResult,
    MiniRegressionSuiteDefinition,
    MiniRegressionSuiteEntry,
    MiniRegressionSuiteReport,
    MiniRegressionSuiteSummary,
)
from operations_center.slice_replay.models import SliceReplayProfile

_runner = CliRunner()

_RUN_TARGET = "operations_center.entrypoints.regression.main.run_mini_regression_suite"
_LOAD_TARGET = "operations_center.entrypoints.regression.main.load_mini_regression_suite"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_suite_def(**kwargs) -> MiniRegressionSuiteDefinition:
    defaults = dict(
        suite_id="test_suite",
        name="Test Suite",
        entries=[
            MiniRegressionSuiteEntry(
                entry_id="entry_001",
                fixture_pack_path="/tmp/pack.json",
                replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                required=True,
            )
        ],
    )
    defaults.update(kwargs)
    return MiniRegressionSuiteDefinition(**defaults)


def _make_suite_report(status: str = "passed") -> MiniRegressionSuiteReport:
    now = datetime.now(UTC)
    return MiniRegressionSuiteReport(
        suite_run_id="run_001",
        suite_id="test_suite",
        suite_name="Test Suite",
        started_at=now,
        ended_at=now,
        status=status,
        entry_results=[
            MiniRegressionEntryResult(
                entry_id="entry_001",
                fixture_pack_id="pack_001",
                fixture_pack_path="/tmp/pack.json",
                replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                required=True,
                status="passed",
                summary="All checks passed",
            )
        ],
        summary=MiniRegressionSuiteSummary(
            total_entries=1,
            required_entries=1,
            optional_entries=0,
            passed_entries=1,
            failed_entries=0,
            error_entries=0,
            skipped_entries=0,
            required_failures=0,
            optional_failures=0,
        ),
    )


def _write_suite_def_file(tmp_path: Path, **kwargs) -> Path:
    suite = _make_suite_def(**kwargs)
    p = tmp_path / "suite.json"
    p.write_text(suite.model_dump_json(indent=2), encoding="utf-8")
    return p


def _write_suite_report_file(tmp_path: Path, status: str = "passed") -> Path:
    report = _make_suite_report(status)
    p = tmp_path / "suite_report.json"
    p.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# cmd_run
# ---------------------------------------------------------------------------

class TestCmdRun:
    def test_run_passed_suite_exits_zero(self, tmp_path: Path):
        suite_path = _write_suite_def_file(tmp_path)
        mock_report = _make_suite_report("passed")
        with patch(_RUN_TARGET, return_value=mock_report):
            result = _runner.invoke(app, [
                "run",
                "--suite", str(suite_path),
                "--output-dir", str(tmp_path / "out"),
            ])
        assert result.exit_code == 0, result.output
        assert "PASSED" in result.output.upper()

    def test_run_failed_suite_exits_nonzero(self, tmp_path: Path):
        suite_path = _write_suite_def_file(tmp_path)
        mock_report = _make_suite_report("failed")
        with patch(_RUN_TARGET, return_value=mock_report):
            result = _runner.invoke(app, [
                "run",
                "--suite", str(suite_path),
                "--output-dir", str(tmp_path / "out"),
            ])
        assert result.exit_code != 0

    def test_run_missing_suite_file_exits_nonzero(self, tmp_path: Path):
        result = _runner.invoke(app, [
            "run",
            "--suite", str(tmp_path / "nonexistent.json"),
            "--output-dir", str(tmp_path / "out"),
        ])
        assert result.exit_code != 0

    def test_run_suite_prints_entry_results(self, tmp_path: Path):
        suite_path = _write_suite_def_file(tmp_path)
        mock_report = _make_suite_report("passed")
        with patch(_RUN_TARGET, return_value=mock_report):
            result = _runner.invoke(app, [
                "run",
                "--suite", str(suite_path),
                "--output-dir", str(tmp_path / "out"),
            ])
        assert "entry_001" in result.output

    def test_run_with_run_id_override(self, tmp_path: Path):
        suite_path = _write_suite_def_file(tmp_path)
        mock_report = _make_suite_report("passed")
        with patch(_RUN_TARGET, return_value=mock_report):
            result = _runner.invoke(app, [
                "run",
                "--suite", str(suite_path),
                "--output-dir", str(tmp_path / "out"),
                "--run-id", "custom_run_id",
            ])
        assert result.exit_code == 0

    def test_run_error_suite_exits_nonzero(self, tmp_path: Path):
        suite_path = _write_suite_def_file(tmp_path)
        mock_report = _make_suite_report("error")
        with patch(_RUN_TARGET, return_value=mock_report):
            result = _runner.invoke(app, [
                "run",
                "--suite", str(suite_path),
                "--output-dir", str(tmp_path / "out"),
            ])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# cmd_inspect
# ---------------------------------------------------------------------------

class TestCmdInspect:
    def test_inspect_written_report_exits_zero(self, tmp_path: Path):
        report_path = _write_suite_report_file(tmp_path, status="passed")
        result = _runner.invoke(app, [
            "inspect",
            "--report", str(report_path),
        ])
        assert result.exit_code == 0, result.output
        assert "test_suite" in result.output

    def test_inspect_failed_report_exits_zero(self, tmp_path: Path):
        report_path = _write_suite_report_file(tmp_path, status="failed")
        result = _runner.invoke(app, [
            "inspect",
            "--report", str(report_path),
        ])
        assert result.exit_code == 0

    def test_inspect_missing_report_exits_nonzero(self, tmp_path: Path):
        result = _runner.invoke(app, [
            "inspect",
            "--report", str(tmp_path / "nonexistent.json"),
        ])
        assert result.exit_code != 0

    def test_inspect_invalid_json_exits_nonzero(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json at all", encoding="utf-8")
        result = _runner.invoke(app, [
            "inspect",
            "--report", str(bad),
        ])
        assert result.exit_code != 0

    def test_inspect_prints_entry_table(self, tmp_path: Path):
        report_path = _write_suite_report_file(tmp_path)
        result = _runner.invoke(app, [
            "inspect",
            "--report", str(report_path),
        ])
        assert "entry_001" in result.output


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------

class TestCmdList:
    def test_list_suite_entries_exits_zero(self, tmp_path: Path):
        suite_path = _write_suite_def_file(tmp_path)
        result = _runner.invoke(app, [
            "list",
            "--suite", str(suite_path),
        ])
        assert result.exit_code == 0, result.output
        assert "entry_001" in result.output

    def test_list_prints_suite_name(self, tmp_path: Path):
        suite_path = _write_suite_def_file(tmp_path)
        result = _runner.invoke(app, [
            "list",
            "--suite", str(suite_path),
        ])
        assert "Test Suite" in result.output

    def test_list_missing_suite_exits_nonzero(self, tmp_path: Path):
        result = _runner.invoke(app, [
            "list",
            "--suite", str(tmp_path / "nonexistent.json"),
        ])
        assert result.exit_code != 0

    def test_list_shows_entry_count(self, tmp_path: Path):
        suite_path = _write_suite_def_file(tmp_path)
        result = _runner.invoke(app, [
            "list",
            "--suite", str(suite_path),
        ])
        assert "1 entries" in result.output
