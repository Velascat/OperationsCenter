# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from operations_center.observer.collectors.check_signal import CheckSignalCollector
from operations_center.observer.service import ObserverContext


def _make_context(tmp_path: Path) -> ObserverContext:
    """Build a minimal ObserverContext pointing at *tmp_path*."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    return ObserverContext(
        repo_path=repo,
        repo_name="test-repo",
        base_branch="main",
        run_id="obs_test",
        observed_at=datetime(2026, 4, 15, tzinfo=UTC),
        source_command="test",
        settings=None,  # type: ignore[arg-type]
        commit_limit=10,
        hotspot_window=7,
        todo_limit=5,
        logs_root=logs,
    )


# ------------------------------------------------------------------
# 1. Existing behaviour: log file present → read it
# ------------------------------------------------------------------


def test_existing_log_file_passed(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    log = ctx.logs_root / "unit_test.log"
    log.write_text("collected 3 items\n3 passed in 0.5s\n")

    sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "passed"
    assert sig.source == str(log)
    assert sig.summary is not None and "passed" in sig.summary


def test_existing_log_file_failed(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    log = ctx.logs_root / "integration_test.log"
    log.write_text("collected 5 items\n2 failed, 3 passed in 1.2s\n")

    sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "failed"


def test_existing_log_file_unknown(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    log = ctx.logs_root / "smoke_test.log"
    log.write_text("some unrecognisable output\n")

    sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "unknown"


# ------------------------------------------------------------------
# 2. Fallback: discoverable (pytest --collect-only succeeds)
# ------------------------------------------------------------------


def test_discoverable_with_pyproject(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    pyproject = ctx.repo_path / "pyproject.toml"
    pyproject.write_text("[tool.pytest.ini_options]\naddopts = '-v'\n")

    collect_stdout = (
        "tests/test_foo.py::test_bar\n"
        "tests/test_foo.py::test_baz\n"
        "\n"
        "2 tests collected\n"
    )
    fake_result = subprocess.CompletedProcess(
        args=["pytest", "--collect-only", "-q", "--no-header"],
        returncode=0,
        stdout=collect_stdout,
        stderr="",
    )

    with patch("operations_center.observer.collectors.check_signal.subprocess.run", return_value=fake_result) as mock_run:
        sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "discoverable"
    assert sig.test_count == 2
    assert sig.source is not None and "collect-only" in sig.source
    assert sig.summary == "2 tests discoverable"
    mock_run.assert_called_once()


def test_discoverable_with_pytest_ini(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pytest.ini").write_text("[pytest]\naddopts = -v\n")

    collect_stdout = "tests/test_a.py::test_one\n\n1 tests collected\n"
    fake_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=collect_stdout, stderr="",
    )

    with patch("operations_center.observer.collectors.check_signal.subprocess.run", return_value=fake_result):
        sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "discoverable"
    assert sig.test_count == 1


# ------------------------------------------------------------------
# 3. Fallback: no_config – pyproject exists but no pytest section
# ------------------------------------------------------------------


def test_no_config_pyproject_without_pytest_section(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pyproject.toml").write_text("[build-system]\nrequires = ['setuptools']\n")

    sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "no_config"


# ------------------------------------------------------------------
# 4. Fallback: no_config – no pyproject.toml, no pytest.ini
# ------------------------------------------------------------------


def test_no_config_no_files(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)

    sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "no_config"


# ------------------------------------------------------------------
# 5. Fallback: unknown on subprocess timeout
# ------------------------------------------------------------------


def test_unknown_on_timeout(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pytest.ini").write_text("[pytest]\n")

    with patch(
        "operations_center.observer.collectors.check_signal.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=5),
    ):
        sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "unknown"


# ------------------------------------------------------------------
# 6. Fallback: unknown on non-zero returncode (e.g. 1)
# ------------------------------------------------------------------


def test_unknown_on_nonzero_returncode(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pytest.ini").write_text("[pytest]\n")

    fake_result = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="ERROR collecting\n",
    )

    with patch("operations_center.observer.collectors.check_signal.subprocess.run", return_value=fake_result):
        sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "unknown"


# ------------------------------------------------------------------
# 7. Fallback: unknown when pytest collects zero tests (rc=5)
# ------------------------------------------------------------------


def test_unknown_on_zero_tests_collected(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    (ctx.repo_path / "pytest.ini").write_text("[pytest]\n")

    fake_result = subprocess.CompletedProcess(
        args=[], returncode=5, stdout="no tests ran in 0.01s\n", stderr="",
    )

    with patch("operations_center.observer.collectors.check_signal.subprocess.run", return_value=fake_result):
        sig = CheckSignalCollector().collect(ctx)

    assert sig.status == "unknown"
