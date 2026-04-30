# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from operations_center.application.validation import ValidationRunner


class TestValidationRunnerNormalCompletion:
    def test_returns_correct_fields(self):
        runner = ValidationRunner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "ok\n"
        mock_proc.stderr = ""

        with patch("operations_center.application.validation.subprocess.run", return_value=mock_proc) as mock_run:
            results = runner.run(["echo hello"], cwd=Path("/tmp"))

        assert len(results) == 1
        r = results[0]
        assert r.command == "echo hello"
        assert r.exit_code == 0
        assert r.stdout == "ok\n"
        assert r.stderr == ""
        assert r.duration_ms >= 0

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("timeout") == 300  # default


class TestValidationRunnerTimeout:
    def test_timeout_produces_exit_code_124(self):
        runner = ValidationRunner()
        exc = subprocess.TimeoutExpired(cmd="sleep 999", timeout=60)
        exc.stdout = None

        with patch("operations_center.application.validation.subprocess.run", side_effect=exc):
            results = runner.run(["sleep 999"], cwd=Path("/tmp"), timeout_seconds=60)

        assert len(results) == 1
        r = results[0]
        assert r.exit_code == 124
        assert "timed out after 60s" in r.stderr
        assert "sleep 999" in r.stderr
        assert r.stdout == ""
        assert r.duration_ms >= 0

    def test_passed_returns_false_on_timeout(self):
        runner = ValidationRunner()
        exc = subprocess.TimeoutExpired(cmd="make test", timeout=10)
        exc.stdout = None

        with patch("operations_center.application.validation.subprocess.run", side_effect=exc):
            results = runner.run(["make test"], cwd=Path("/tmp"), timeout_seconds=10)

        assert not runner.passed(results)


class TestValidationRunnerNoTimeout:
    def test_none_timeout_passed_to_subprocess(self):
        runner = ValidationRunner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("operations_center.application.validation.subprocess.run", return_value=mock_proc) as mock_run:
            runner.run(["true"], cwd=Path("/tmp"), timeout_seconds=None)

        assert mock_run.call_args.kwargs.get("timeout") is None


class TestValidationRunnerTimeoutStdoutBytes:
    def test_timeout_decodes_partial_stdout_bytes(self):
        runner = ValidationRunner()
        exc = subprocess.TimeoutExpired(cmd="sleep 999", timeout=60)
        exc.stdout = b"partial output\nline two"

        with patch("operations_center.application.validation.subprocess.run", side_effect=exc):
            results = runner.run(["sleep 999"], cwd=Path("/tmp"), timeout_seconds=60)

        assert len(results) == 1
        result = results[0]
        assert result.exit_code == 124
        assert result.stdout == "partial output\nline two"
        assert "timed out after 60s" in result.stderr


class TestValidationRunnerMixedCommandOutcomes:
    def test_mixed_pass_timeout_pass_returns_all_results(self):
        runner = ValidationRunner()
        mock_proc_ok = MagicMock()
        mock_proc_ok.returncode = 0
        mock_proc_ok.stdout = "first ok\n"
        mock_proc_ok.stderr = ""

        timeout_exc = subprocess.TimeoutExpired(cmd="sleep 999", timeout=30)
        timeout_exc.stdout = None

        mock_proc_ok2 = MagicMock()
        mock_proc_ok2.returncode = 0
        mock_proc_ok2.stdout = "third ok\n"
        mock_proc_ok2.stderr = ""

        with patch(
            "operations_center.application.validation.subprocess.run",
            side_effect=[mock_proc_ok, timeout_exc, mock_proc_ok2],
        ):
            results = runner.run(
                ["echo first", "sleep 999", "echo third"],
                cwd=Path("/tmp"),
                timeout_seconds=30,
            )

        assert len(results) == 3
        assert [result.command for result in results] == ["echo first", "sleep 999", "echo third"]
        assert [result.exit_code for result in results] == [0, 124, 0]
        assert results[0].stdout == "first ok\n"
        assert results[1].stdout == ""
        assert "timed out after 30s" in results[1].stderr
        assert results[2].stdout == "third ok\n"
