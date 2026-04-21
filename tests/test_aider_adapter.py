# tests/test_aider_adapter.py
"""Unit tests for AiderAdapter."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from control_plane.adapters.executor.aider import AiderAdapter
from control_plane.adapters.executor.protocol import ExecutorTask
from control_plane.config.settings import AiderSettings


def _make_adapter(switchboard_url: str = "http://localhost:20401") -> AiderAdapter:
    settings = AiderSettings(binary="aider", profile="capable", timeout_seconds=60)
    return AiderAdapter(settings, switchboard_url=switchboard_url)


def _make_task(tmp_path: Path, goal: str = "Add a test", constraints: str = "") -> ExecutorTask:
    return ExecutorTask(goal=goal, repo_path=tmp_path, constraints=constraints)


def _completed_process(returncode: int = 0, stdout: str = "done", stderr: str = ""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# name()
# ---------------------------------------------------------------------------


def test_name_returns_aider():
    adapter = _make_adapter()
    assert adapter.name() == "aider"


# ---------------------------------------------------------------------------
# Executor protocol satisfied
# ---------------------------------------------------------------------------


def test_aider_adapter_satisfies_executor_protocol():
    from control_plane.adapters.executor.protocol import Executor

    adapter = _make_adapter()
    assert isinstance(adapter, Executor)


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_execute_calls_aider_binary(mock_run, tmp_path: Path):
    mock_run.return_value = _completed_process()
    adapter = _make_adapter()
    adapter.execute(_make_task(tmp_path))

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "aider"


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_execute_passes_model_flag(mock_run, tmp_path: Path):
    mock_run.return_value = _completed_process()
    adapter = _make_adapter()
    adapter.execute(_make_task(tmp_path))

    cmd = mock_run.call_args[0][0]
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "openai/capable"


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_execute_passes_message_flag(mock_run, tmp_path: Path):
    mock_run.return_value = _completed_process()
    adapter = _make_adapter()
    adapter.execute(_make_task(tmp_path, goal="Fix the bug"))

    cmd = mock_run.call_args[0][0]
    msg_idx = cmd.index("--message")
    assert "Fix the bug" in cmd[msg_idx + 1]


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_execute_passes_yes_flag(mock_run, tmp_path: Path):
    mock_run.return_value = _completed_process()
    adapter = _make_adapter()
    adapter.execute(_make_task(tmp_path))

    cmd = mock_run.call_args[0][0]
    assert "--yes" in cmd


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_execute_profile_from_task_metadata(mock_run, tmp_path: Path):
    mock_run.return_value = _completed_process()
    adapter = _make_adapter()
    task = _make_task(tmp_path)
    task.metadata["profile"] = "fast"
    adapter.execute(task)

    cmd = mock_run.call_args[0][0]
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "openai/fast"


# ---------------------------------------------------------------------------
# SwitchBoard environment variable
# ---------------------------------------------------------------------------


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_execute_sets_openai_api_base(mock_run, tmp_path: Path):
    mock_run.return_value = _completed_process()
    adapter = _make_adapter(switchboard_url="http://sb:20401")
    adapter.execute(_make_task(tmp_path))

    env = mock_run.call_args.kwargs["env"]
    assert env["OPENAI_API_BASE"] == "http://sb:20401/v1"


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_execute_sets_placeholder_api_key(mock_run, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mock_run.return_value = _completed_process()
    adapter = _make_adapter()
    adapter.execute(_make_task(tmp_path))

    env = mock_run.call_args.kwargs["env"]
    assert "OPENAI_API_KEY" in env
    assert env["OPENAI_API_KEY"]  # non-empty


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_execute_no_openai_api_base_without_switchboard(mock_run, tmp_path: Path):
    mock_run.return_value = _completed_process()
    settings = AiderSettings(binary="aider")
    adapter = AiderAdapter(settings, switchboard_url="")
    adapter.execute(_make_task(tmp_path))

    env = mock_run.call_args.kwargs["env"]
    assert "OPENAI_API_BASE" not in env


# ---------------------------------------------------------------------------
# Result normalization
# ---------------------------------------------------------------------------


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_success_returns_success_true(mock_run, tmp_path: Path):
    mock_run.return_value = _completed_process(returncode=0, stdout="all done")
    result = _make_adapter().execute(_make_task(tmp_path))

    assert result.success is True
    assert result.exit_code == 0
    assert result.executor == "aider"
    assert "all done" in result.output


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_failure_returns_success_false(mock_run, tmp_path: Path):
    mock_run.return_value = _completed_process(returncode=1, stderr="error: file not found")
    result = _make_adapter().execute(_make_task(tmp_path))

    assert result.success is False
    assert result.exit_code == 1
    assert "error: file not found" in result.output


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_metadata_includes_command_and_model(mock_run, tmp_path: Path):
    mock_run.return_value = _completed_process()
    result = _make_adapter().execute(_make_task(tmp_path))

    assert "command" in result.metadata
    assert "model" in result.metadata


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@patch(
    "control_plane.adapters.executor.aider.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd=["aider"], timeout=60),
)
def test_timeout_returns_failure(mock_run, tmp_path: Path):
    result = _make_adapter().execute(_make_task(tmp_path))

    assert result.success is False
    assert result.metadata.get("timeout_hit") is True
    assert "Timed out" in result.output


@patch(
    "control_plane.adapters.executor.aider.subprocess.run",
    side_effect=FileNotFoundError("no such file"),
)
def test_missing_binary_returns_failure(mock_run, tmp_path: Path):
    result = _make_adapter().execute(_make_task(tmp_path))

    assert result.success is False
    assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# Constraints appended to goal
# ---------------------------------------------------------------------------


@patch("control_plane.adapters.executor.aider.subprocess.run")
def test_constraints_appended_to_message(mock_run, tmp_path: Path):
    mock_run.return_value = _completed_process()
    adapter = _make_adapter()
    task = _make_task(tmp_path, goal="Add auth", constraints="Only modify src/auth/")
    adapter.execute(task)

    cmd = mock_run.call_args[0][0]
    msg_idx = cmd.index("--message")
    msg = cmd[msg_idx + 1]
    assert "Add auth" in msg
    assert "Only modify src/auth/" in msg
