# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""CLI tests for operations-center-replay commands.

Covers run / inspect using typer.testing.CliRunner.
Slice replay execution is monkeypatched — no real fixture packs are read.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from operations_center.entrypoints.replay.main import app
from operations_center.slice_replay.models import SliceReplayProfile

_runner = CliRunner()

_RUN_REPLAY_TARGET = "operations_center.entrypoints.replay.main.run_slice_replay"
_WRITE_REPORT_TARGET = "operations_center.entrypoints.replay.main.write_replay_report"
_LOAD_REPORT_TARGET = "operations_center.entrypoints.replay.main.load_replay_report"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_report(status: str = "passed") -> MagicMock:
    report = MagicMock()
    report.replay_id = "replay_001"
    report.fixture_pack_id = "pack_001"
    report.replay_profile = SliceReplayProfile.FIXTURE_INTEGRITY
    report.status = status
    report.summary = f"All checks {status}"
    report.check_results = []
    return report


def _make_fixture_pack_file(tmp_path: Path) -> Path:
    p = tmp_path / "fixture_pack.json"
    p.write_text("{}", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# cmd_run
# ---------------------------------------------------------------------------

class TestCmdRun:
    def test_run_passed(self, tmp_path: Path):
        fp = _make_fixture_pack_file(tmp_path)
        report = _make_mock_report(status="passed")
        out_dir = tmp_path / "reports"
        with (
            patch(_RUN_REPLAY_TARGET, return_value=report),
            patch(_WRITE_REPORT_TARGET, return_value=out_dir / "report.json"),
        ):
            out = _runner.invoke(
                app,
                ["run", "--fixture-pack", str(fp), "--output-dir", str(out_dir)],
            )
        assert out.exit_code == 0
        assert "PASSED" in out.output

    def test_run_failed_exits_code_1(self, tmp_path: Path):
        fp = _make_fixture_pack_file(tmp_path)
        report = _make_mock_report(status="failed")
        with (
            patch(_RUN_REPLAY_TARGET, return_value=report),
            patch(_WRITE_REPORT_TARGET, return_value=None),
        ):
            out = _runner.invoke(
                app,
                ["run", "--fixture-pack", str(fp), "--output-dir", str(tmp_path)],
            )
        assert out.exit_code == 1

    def test_run_error_status_exits_code_1(self, tmp_path: Path):
        fp = _make_fixture_pack_file(tmp_path)
        report = _make_mock_report(status="error")
        with (
            patch(_RUN_REPLAY_TARGET, return_value=report),
            patch(_WRITE_REPORT_TARGET, return_value=None),
        ):
            out = _runner.invoke(
                app,
                ["run", "--fixture-pack", str(fp), "--output-dir", str(tmp_path)],
            )
        assert out.exit_code == 1

    def test_run_replay_input_error_exits_code_3(self, tmp_path: Path):
        from operations_center.slice_replay import ReplayInputError
        fp = _make_fixture_pack_file(tmp_path)
        with patch(_RUN_REPLAY_TARGET, side_effect=ReplayInputError("bad input")):
            out = _runner.invoke(
                app,
                ["run", "--fixture-pack", str(fp), "--output-dir", str(tmp_path)],
            )
        assert out.exit_code == 3
        assert "Replay error" in out.output

    def test_run_report_write_warning_still_exits_0(self, tmp_path: Path):
        fp = _make_fixture_pack_file(tmp_path)
        report = _make_mock_report(status="passed")
        with (
            patch(_RUN_REPLAY_TARGET, return_value=report),
            patch(_WRITE_REPORT_TARGET, side_effect=OSError("disk full")),
        ):
            out = _runner.invoke(
                app,
                ["run", "--fixture-pack", str(fp), "--output-dir", str(tmp_path)],
            )
        assert out.exit_code == 0
        assert "Warning" in out.output or "PASSED" in out.output


# ---------------------------------------------------------------------------
# cmd_inspect
# ---------------------------------------------------------------------------

class TestCmdInspect:
    def test_inspect_passed_report(self, tmp_path: Path):
        report_file = tmp_path / "replay_report.json"
        report_file.write_text("{}", encoding="utf-8")
        report = _make_mock_report(status="passed")
        with patch(_LOAD_REPORT_TARGET, return_value=report):
            out = _runner.invoke(app, ["inspect", "--report", str(report_file)])
        assert out.exit_code == 0
        assert "replay_001" in out.output
        assert "pack_001" in out.output

    def test_inspect_not_found_exits_code_1(self):
        with patch(_LOAD_REPORT_TARGET, side_effect=FileNotFoundError("missing")):
            out = _runner.invoke(app, ["inspect", "--report", "/no/such/report.json"])
        assert out.exit_code == 1
        assert "Not found" in out.output

    def test_inspect_load_error_exits_code_2(self, tmp_path: Path):
        from operations_center.slice_replay import ReplayReportLoadError
        report_file = tmp_path / "replay_report.json"
        report_file.write_text("{}", encoding="utf-8")
        with patch(_LOAD_REPORT_TARGET, side_effect=ReplayReportLoadError("corrupted")):
            out = _runner.invoke(app, ["inspect", "--report", str(report_file)])
        assert out.exit_code == 2
        assert "Load error" in out.output

    def test_inspect_failed_report_shows_failed_status(self, tmp_path: Path):
        report_file = tmp_path / "replay_report.json"
        report_file.write_text("{}", encoding="utf-8")
        report = _make_mock_report(status="failed")
        with patch(_LOAD_REPORT_TARGET, return_value=report):
            out = _runner.invoke(app, ["inspect", "--report", str(report_file)])
        assert out.exit_code == 0
        assert "failed" in out.output.lower()
