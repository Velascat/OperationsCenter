"""Tests for ValidationRunner timeout and subprocess handling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from control_plane.application.validation import ValidationRunner
from control_plane.domain import ValidationResult


class TestValidationRunnerTimeout:
    """Tests for the timeout_seconds parameter on ValidationRunner.run()."""

    def test_default_timeout_is_600(self, tmp_path: Path) -> None:
        """subprocess.run is called with timeout=600 by default."""
        runner = ValidationRunner()
        with patch("control_plane.application.validation.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args="echo ok", returncode=0, stdout="ok\n", stderr=""
            )
            runner.run(["echo ok"], cwd=tmp_path)
            mock_run.assert_called_once()
            assert mock_run.call_args.kwargs["timeout"] == 600

    def test_custom_timeout_is_forwarded(self, tmp_path: Path) -> None:
        """A caller-supplied timeout_seconds value reaches subprocess.run."""
        runner = ValidationRunner()
        with patch("control_plane.application.validation.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args="echo ok", returncode=0, stdout="ok\n", stderr=""
            )
            runner.run(["echo ok"], cwd=tmp_path, timeout_seconds=60)
            assert mock_run.call_args.kwargs["timeout"] == 60

    def test_timeout_expired_produces_failure_result(self, tmp_path: Path) -> None:
        """When a command times out, it is recorded as a failed ValidationResult."""
        runner = ValidationRunner()
        with patch("control_plane.application.validation.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep 999", timeout=5)
            results = runner.run(["sleep 999"], cwd=tmp_path, timeout_seconds=5)

        assert len(results) == 1
        r = results[0]
        assert r.command == "sleep 999"
        assert r.exit_code == -1
        assert "timed out after 5 seconds" in r.stderr.lower()
        assert r.duration_ms >= 0

    def test_timeout_does_not_stop_remaining_commands(self, tmp_path: Path) -> None:
        """After a timeout, subsequent commands still execute."""
        runner = ValidationRunner()
        side_effects = [
            subprocess.TimeoutExpired(cmd="sleep 999", timeout=5),
            subprocess.CompletedProcess(args="echo ok", returncode=0, stdout="ok\n", stderr=""),
        ]
        with patch("control_plane.application.validation.subprocess.run") as mock_run:
            mock_run.side_effect = side_effects
            results = runner.run(["sleep 999", "echo ok"], cwd=tmp_path, timeout_seconds=5)

        assert len(results) == 2
        assert results[0].exit_code == -1  # timed-out
        assert results[1].exit_code == 0   # succeeded

    def test_passed_returns_false_on_timeout(self, tmp_path: Path) -> None:
        """ValidationRunner.passed() treats timed-out results as failures."""
        results = [
            ValidationResult(command="sleep 999", exit_code=-1, stdout="", stderr="timed out", duration_ms=5000),
        ]
        assert ValidationRunner.passed(results) is False

    def test_normal_execution_still_works(self, tmp_path: Path) -> None:
        """A basic command runs successfully and returns expected results."""
        runner = ValidationRunner()
        results = runner.run(["echo hello"], cwd=tmp_path)
        assert len(results) == 1
        assert results[0].exit_code == 0
        assert "hello" in results[0].stdout
