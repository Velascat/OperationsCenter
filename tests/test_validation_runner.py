from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from control_plane.application.validation import ValidationRunner


class TestValidationRunnerNormalCompletion:
    def test_returns_correct_fields(self):
        runner = ValidationRunner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "ok\n"
        mock_proc.stderr = ""

        with patch("control_plane.application.validation.subprocess.run", return_value=mock_proc) as mock_run:
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

        with patch("control_plane.application.validation.subprocess.run", side_effect=exc):
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

        with patch("control_plane.application.validation.subprocess.run", side_effect=exc):
            results = runner.run(["make test"], cwd=Path("/tmp"), timeout_seconds=10)

        assert not runner.passed(results)


class TestValidationRunnerNoTimeout:
    def test_none_timeout_passed_to_subprocess(self):
        runner = ValidationRunner()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("control_plane.application.validation.subprocess.run", return_value=mock_proc) as mock_run:
            runner.run(["true"], cwd=Path("/tmp"), timeout_seconds=None)

        assert mock_run.call_args.kwargs.get("timeout") is None
