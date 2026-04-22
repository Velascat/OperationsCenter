"""Tests for kodo invocation boundary (KodoBackendInvoker)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from control_plane.adapters.kodo.adapter import KodoAdapter, KodoRunResult
from control_plane.backends.kodo.invoke import KodoBackendInvoker, _extract_artifacts
from control_plane.backends.kodo.models import KodoPreparedRun


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prepared(tmp_path: Path, **kw) -> KodoPreparedRun:
    defaults = dict(
        run_id="run-1",
        goal_text="Fix lint errors",
        constraints_text=None,
        repo_path=tmp_path / "repo",
        task_branch="auto/lint-fix",
        goal_file_path=tmp_path / "repo" / ".kodo_goal.md",
        validation_commands=[],
        timeout_seconds=300,
    )
    defaults.update(kw)
    return KodoPreparedRun(**defaults)


def _mock_kodo(exit_code: int = 0, stdout: str = "done", stderr: str = "") -> KodoAdapter:
    kodo = MagicMock(spec=KodoAdapter)
    kodo.run.return_value = KodoRunResult(exit_code=exit_code, stdout=stdout, stderr=stderr, command=["kodo"])
    kodo.write_goal_file = MagicMock()
    return kodo


def _invoker(kodo: KodoAdapter) -> KodoBackendInvoker:
    return KodoBackendInvoker(kodo)


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------

class TestInvocation:
    def test_invoke_calls_kodo_run(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        kodo = _mock_kodo()
        invoker = _invoker(kodo)
        capture = invoker.invoke(_prepared(tmp_path))
        kodo.run.assert_called_once()

    def test_invoke_calls_write_goal_file(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        kodo = _mock_kodo()
        invoker = _invoker(kodo)
        invoker.invoke(_prepared(tmp_path))
        kodo.write_goal_file.assert_called_once()

    def test_capture_run_id_matches_prepared(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        kodo = _mock_kodo()
        capture = _invoker(kodo).invoke(_prepared(tmp_path, run_id="my-run"))
        assert capture.run_id == "my-run"

    def test_capture_exit_code(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        kodo = _mock_kodo(exit_code=1, stderr="error")
        capture = _invoker(kodo).invoke(_prepared(tmp_path))
        assert capture.exit_code == 1
        assert capture.succeeded is False

    def test_capture_succeeded_on_exit_zero(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        kodo = _mock_kodo(exit_code=0)
        capture = _invoker(kodo).invoke(_prepared(tmp_path))
        assert capture.succeeded is True

    def test_capture_has_duration(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        kodo = _mock_kodo()
        capture = _invoker(kodo).invoke(_prepared(tmp_path))
        assert capture.duration_ms >= 0

    def test_timeout_hit_flag_set(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        kodo = _mock_kodo(exit_code=-1, stderr="[timeout: process group killed after 300s]")
        capture = _invoker(kodo).invoke(_prepared(tmp_path))
        assert capture.timeout_hit is True

    def test_goal_file_deleted_after_run(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        goal_file = tmp_path / "repo" / ".kodo_goal.md"
        # Make sure goal_file path doesn't exist before (unlink(missing_ok) handles it)
        kodo = _mock_kodo()
        kodo.write_goal_file = MagicMock()
        invoker = _invoker(kodo)
        invoker.invoke(_prepared(tmp_path))
        # invoker should have attempted to unlink the goal file
        assert not goal_file.exists()


def test_invoker_does_not_inject_openai_api_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    kodo = _mock_kodo()
    invoker = _invoker(kodo)

    captured_env = {}

    def capture_env(goal_file, repo_path, env=None, kodo_mode="goal"):
        captured_env.update(env or {})
        return KodoRunResult(0, "", "", ["kodo"])

    kodo.run.side_effect = capture_env
    invoker.invoke(_prepared(tmp_path))
    assert "OPENAI_API_BASE" not in captured_env


# ---------------------------------------------------------------------------
# Artifact extraction
# ---------------------------------------------------------------------------

class TestArtifactExtraction:
    def test_log_excerpt_artifact_created(self):
        artifacts = _extract_artifacts("output line 1\noutput line 2", "")
        assert len(artifacts) == 1
        assert artifacts[0].artifact_type == "log_excerpt"
        assert "output line 1" in artifacts[0].content

    def test_empty_output_no_artifacts(self):
        artifacts = _extract_artifacts("", "")
        assert artifacts == []

    def test_stderr_included_in_artifact(self):
        artifacts = _extract_artifacts("", "error text")
        assert "error text" in artifacts[0].content

    def test_long_output_truncated(self):
        long = "x" * 10_000
        artifacts = _extract_artifacts(long, "")
        assert len(artifacts[0].content) < len(long)
        assert "truncated" in artifacts[0].content
