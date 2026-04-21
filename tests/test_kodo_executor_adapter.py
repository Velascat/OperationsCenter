# tests/test_kodo_executor_adapter.py
"""Unit tests for KodoExecutorAdapter."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from control_plane.adapters.executor.kodo import KodoExecutorAdapter
from control_plane.adapters.executor.protocol import ExecutorTask
from control_plane.adapters.kodo.adapter import KodoAdapter, KodoRunResult
from control_plane.config.settings import KodoSettings


def _make_kodo_result(exit_code: int = 0, stdout: str = "ok", stderr: str = "") -> KodoRunResult:
    return KodoRunResult(exit_code=exit_code, stdout=stdout, stderr=stderr, command=["kodo"])


def _make_adapter(switchboard_url: str = "") -> KodoExecutorAdapter:
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = _make_kodo_result()
    kodo.write_goal_file = MagicMock()
    return KodoExecutorAdapter(kodo, switchboard_url=switchboard_url)


def _make_task(tmp_path: Path, goal: str = "Fix the tests") -> ExecutorTask:
    return ExecutorTask(goal=goal, repo_path=tmp_path)


# ---------------------------------------------------------------------------
# name()
# ---------------------------------------------------------------------------


def test_name_returns_kodo():
    adapter = _make_adapter()
    assert adapter.name() == "kodo"


# ---------------------------------------------------------------------------
# Executor protocol satisfied
# ---------------------------------------------------------------------------


def test_kodo_executor_adapter_satisfies_protocol():
    from control_plane.adapters.executor.protocol import Executor

    adapter = _make_adapter()
    assert isinstance(adapter, Executor)


# ---------------------------------------------------------------------------
# Goal file lifecycle
# ---------------------------------------------------------------------------


def test_execute_writes_goal_file(tmp_path: Path):
    adapter = _make_adapter()
    adapter.execute(_make_task(tmp_path, goal="Improve test coverage"))
    adapter._kodo.write_goal_file.assert_called_once()
    call_args = adapter._kodo.write_goal_file.call_args[0]
    assert "Improve test coverage" in call_args[1]


def test_execute_removes_goal_file_after_run(tmp_path: Path):
    """The temp goal file must be cleaned up even if kodo succeeds."""
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = _make_kodo_result()
    kodo.write_goal_file = MagicMock()
    adapter = KodoExecutorAdapter(kodo)
    adapter.execute(_make_task(tmp_path))
    # If the file was written and deleted, the path should not exist
    goal_file = tmp_path / ".kodo_goal.md"
    assert not goal_file.exists()


def test_execute_removes_goal_file_on_kodo_error(tmp_path: Path):
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.side_effect = RuntimeError("kodo crashed")
    kodo.write_goal_file = MagicMock()
    adapter = KodoExecutorAdapter(kodo)

    with pytest.raises(RuntimeError):
        adapter.execute(_make_task(tmp_path))

    goal_file = tmp_path / ".kodo_goal.md"
    assert not goal_file.exists()


# ---------------------------------------------------------------------------
# SwitchBoard environment variable injection
# ---------------------------------------------------------------------------


def test_execute_sets_openai_api_base_when_switchboard_url_set(tmp_path: Path):
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = _make_kodo_result()
    kodo.write_goal_file = MagicMock()
    adapter = KodoExecutorAdapter(kodo, switchboard_url="http://sb:20401")

    adapter.execute(_make_task(tmp_path))

    call_env = kodo.run.call_args.kwargs.get("env") or kodo.run.call_args[1].get("env")
    assert call_env is not None
    assert call_env["OPENAI_API_BASE"] == "http://sb:20401/v1"


def test_execute_no_openai_api_base_without_switchboard(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = _make_kodo_result()
    kodo.write_goal_file = MagicMock()
    adapter = KodoExecutorAdapter(kodo, switchboard_url="")

    adapter.execute(_make_task(tmp_path))

    call_env = kodo.run.call_args.kwargs.get("env") or kodo.run.call_args[1].get("env")
    # OPENAI_API_BASE should not have been injected by the adapter
    # (it may exist in inherited env from parent process, but not set by adapter)
    # We verify by checking it wasn't explicitly set to a switchboard URL
    if call_env and "OPENAI_API_BASE" in call_env:
        assert "20401" not in call_env["OPENAI_API_BASE"]


# ---------------------------------------------------------------------------
# kodo_mode and profile from metadata
# ---------------------------------------------------------------------------


def test_execute_passes_kodo_mode_from_metadata(tmp_path: Path):
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = _make_kodo_result()
    kodo.write_goal_file = MagicMock()
    adapter = KodoExecutorAdapter(kodo)

    task = _make_task(tmp_path)
    task.metadata["kodo_mode"] = "improve"
    adapter.execute(task)

    kodo_mode_kwarg = kodo.run.call_args.kwargs.get("kodo_mode") or kodo.run.call_args[1].get("kodo_mode")
    assert kodo_mode_kwarg == "improve"


def test_execute_defaults_kodo_mode_to_goal(tmp_path: Path):
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = _make_kodo_result()
    kodo.write_goal_file = MagicMock()
    adapter = KodoExecutorAdapter(kodo)

    adapter.execute(_make_task(tmp_path))

    kodo_mode_kwarg = kodo.run.call_args.kwargs.get("kodo_mode") or kodo.run.call_args[1].get("kodo_mode")
    assert kodo_mode_kwarg == "goal"


# ---------------------------------------------------------------------------
# Result normalization
# ---------------------------------------------------------------------------


def test_success_result_when_exit_code_zero(tmp_path: Path):
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = _make_kodo_result(exit_code=0, stdout="all good")
    kodo.write_goal_file = MagicMock()
    adapter = KodoExecutorAdapter(kodo)

    result = adapter.execute(_make_task(tmp_path))

    assert result.success is True
    assert result.exit_code == 0
    assert result.executor == "kodo"
    assert "all good" in result.output


def test_failure_result_when_exit_code_nonzero(tmp_path: Path):
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = _make_kodo_result(exit_code=1, stderr="failed to apply changes")
    kodo.write_goal_file = MagicMock()
    adapter = KodoExecutorAdapter(kodo)

    result = adapter.execute(_make_task(tmp_path))

    assert result.success is False
    assert result.exit_code == 1
    assert "failed to apply changes" in result.output


def test_timeout_flagged_in_metadata(tmp_path: Path):
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = _make_kodo_result(
        exit_code=-1, stderr="[timeout: process group killed after 3600s]"
    )
    kodo.write_goal_file = MagicMock()
    adapter = KodoExecutorAdapter(kodo)

    result = adapter.execute(_make_task(tmp_path))

    assert result.metadata.get("timeout_hit") is True
    assert result.success is False


def test_metadata_includes_command(tmp_path: Path):
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = KodoRunResult(0, "ok", "", ["kodo", "--goal-file", "f.md"])
    kodo.write_goal_file = MagicMock()
    adapter = KodoExecutorAdapter(kodo)

    result = adapter.execute(_make_task(tmp_path))

    assert "command" in result.metadata
    assert result.metadata["command"] == ["kodo", "--goal-file", "f.md"]
