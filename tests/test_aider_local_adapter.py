# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for AiderLocalBackendAdapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from operations_center.backends.aider_local.adapter import AiderLocalBackendAdapter
from operations_center.config.settings import AiderLocalSettings
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.contracts.execution import ExecutionRequest


def _settings(**kw) -> AiderLocalSettings:
    defaults = dict(
        binary="aider",
        model="ollama/qwen2.5-coder:3b",
        ollama_base_url="http://localhost:11434",
        timeout_seconds=60,
    )
    defaults.update(kw)
    return AiderLocalSettings(**defaults)


def _request(tmp_path: Path, **kw) -> ExecutionRequest:
    defaults = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="Fix the lint error in main.py",
        repo_key="svc",
        clone_url="https://git.example.com/svc.git",
        base_branch="main",
        task_branch="auto/fix-1",
        workspace_path=tmp_path,
    )
    defaults.update(kw)
    return ExecutionRequest(**defaults)


# ---------------------------------------------------------------------------
# Command builder
# ---------------------------------------------------------------------------

class TestBuildCommand:
    def test_includes_model_flag(self) -> None:
        adapter = AiderLocalBackendAdapter(_settings())
        cmd = adapter._build_command("/tmp/msg.txt")
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "ollama/qwen2.5-coder:3b"

    def test_includes_api_base(self) -> None:
        adapter = AiderLocalBackendAdapter(_settings())
        cmd = adapter._build_command("/tmp/msg.txt")
        assert "--api-base" in cmd
        idx = cmd.index("--api-base")
        assert cmd[idx + 1] == "http://localhost:11434"

    def test_uses_yes_always(self) -> None:
        adapter = AiderLocalBackendAdapter(_settings())
        cmd = adapter._build_command("/tmp/msg.txt")
        assert "--yes-always" in cmd

    def test_uses_message_file_not_message(self) -> None:
        adapter = AiderLocalBackendAdapter(_settings())
        cmd = adapter._build_command("/tmp/msg.txt")
        assert "--message-file" in cmd
        assert "--message" not in cmd  # not the inline form
        idx = cmd.index("--message-file")
        assert cmd[idx + 1] == "/tmp/msg.txt"

    def test_extra_args_appended(self) -> None:
        adapter = AiderLocalBackendAdapter(_settings(extra_args=["--no-git"]))
        cmd = adapter._build_command("/tmp/msg.txt")
        assert "--no-git" in cmd

    def test_custom_model(self) -> None:
        adapter = AiderLocalBackendAdapter(_settings(model="ollama/codellama:7b"))
        cmd = adapter._build_command("/tmp/msg.txt")
        assert "ollama/codellama:7b" in cmd


# ---------------------------------------------------------------------------
# Subprocess success / failure
# ---------------------------------------------------------------------------

class TestExecute:
    def _mock_git_diff(self, returncode: int = 0, stdout: str = "") -> MagicMock:
        mock = MagicMock()
        mock.returncode = returncode
        mock.stdout = stdout
        mock.stderr = ""
        return mock

    def test_success_returns_succeeded_status(self, tmp_path: Path) -> None:
        adapter = AiderLocalBackendAdapter(_settings())
        req = _request(tmp_path)

        aider_result = MagicMock()
        aider_result.returncode = 0
        aider_result.stdout = "Changes applied."
        aider_result.stderr = ""

        git_result = self._mock_git_diff(stdout="M\tsrc/main.py\n")

        with patch("subprocess.run", side_effect=[aider_result, git_result]):
            result = adapter.execute(req)

        assert result.success is True
        assert result.status == ExecutionStatus.SUCCEEDED
        assert result.run_id == req.run_id

    def test_failure_returns_failed_status(self, tmp_path: Path) -> None:
        adapter = AiderLocalBackendAdapter(_settings())
        req = _request(tmp_path)

        aider_result = MagicMock()
        aider_result.returncode = 1
        aider_result.stdout = ""
        aider_result.stderr = "Error: model not found"

        git_result = self._mock_git_diff()

        with patch("subprocess.run", side_effect=[aider_result, git_result]):
            result = adapter.execute(req)

        assert result.success is False
        assert result.status == ExecutionStatus.FAILED
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR

    def test_timeout_returns_timed_out_status(self, tmp_path: Path) -> None:
        adapter = AiderLocalBackendAdapter(_settings(timeout_seconds=1))
        req = _request(tmp_path)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="aider", timeout=1)):
            result = adapter.execute(req)

        assert result.success is False
        assert result.status == ExecutionStatus.TIMED_OUT
        assert result.failure_category == FailureReasonCategory.TIMEOUT

    def test_missing_binary_returns_failed(self, tmp_path: Path) -> None:
        adapter = AiderLocalBackendAdapter(_settings(binary="/nonexistent/aider"))
        req = _request(tmp_path)

        with patch("subprocess.run", side_effect=FileNotFoundError("not found")):
            result = adapter.execute(req)

        assert result.success is False
        assert result.status == ExecutionStatus.FAILED
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR

    def test_changed_files_collected(self, tmp_path: Path) -> None:
        adapter = AiderLocalBackendAdapter(_settings())
        req = _request(tmp_path)

        aider_result = MagicMock(returncode=0, stdout="done", stderr="")
        git_result = self._mock_git_diff(stdout="M\tsrc/foo.py\nA\tsrc/bar.py\n")

        with patch("subprocess.run", side_effect=[aider_result, git_result]):
            result = adapter.execute(req)

        assert len(result.changed_files) == 2
        paths = {f.path for f in result.changed_files}
        assert "src/foo.py" in paths
        assert "src/bar.py" in paths

    def test_constraints_appended_to_goal(self, tmp_path: Path) -> None:
        adapter = AiderLocalBackendAdapter(_settings())
        req = _request(tmp_path, goal_text="Fix bug", constraints_text="Do not change tests")

        captured_message: list[str] = []

        def capture_run(cmd, **kwargs):
            if "--message-file" in cmd:
                idx = cmd.index("--message-file")
                msg_path = cmd[idx + 1]
                with open(msg_path) as fh:
                    captured_message.append(fh.read())
                mock = MagicMock(returncode=0, stdout="ok", stderr="")
                return mock
            # git diff call
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=capture_run):
            adapter.execute(req)

        assert len(captured_message) == 1
        assert "Fix bug" in captured_message[0]
        assert "Do not change tests" in captured_message[0]

    def test_no_openai_key_set_in_env(self, tmp_path: Path) -> None:
        adapter = AiderLocalBackendAdapter(_settings())
        req = _request(tmp_path)

        captured_env: list[dict] = []

        def capture_run(cmd, **kwargs):
            if "--message-file" in cmd:
                captured_env.append(kwargs.get("env", {}))
                return MagicMock(returncode=0, stdout="ok", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        import os
        env_without_key = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch("subprocess.run", side_effect=capture_run), \
             patch("os.environ", env_without_key):
            adapter.execute(req)

        assert len(captured_env) == 1
        # A dummy key is injected so aider doesn't warn
        assert "OPENAI_API_KEY" in captured_env[0]

    def test_branch_name_from_request(self, tmp_path: Path) -> None:
        adapter = AiderLocalBackendAdapter(_settings())
        req = _request(tmp_path, task_branch="auto/my-branch")

        aider_result = MagicMock(returncode=0, stdout="ok", stderr="")
        git_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=[aider_result, git_result]):
            result = adapter.execute(req)

        assert result.branch_name == "auto/my-branch"
        assert result.branch_pushed is False


# ---------------------------------------------------------------------------
# BackendName enum
# ---------------------------------------------------------------------------

def test_backend_name_aider_local_exists() -> None:
    from operations_center.contracts.enums import BackendName
    assert BackendName.AIDER_LOCAL.value == "aider_local"


# ---------------------------------------------------------------------------
# Factory registration
# ---------------------------------------------------------------------------

def test_factory_registers_aider_local(tmp_path: Path) -> None:
    from operations_center.backends.factory import CanonicalBackendRegistry
    from operations_center.contracts.enums import BackendName

    from operations_center.config.settings import Settings, PlaneSettings, GitSettings, KodoSettings
    settings = Settings(
        plane=PlaneSettings(
            base_url="http://plane.local",
            api_token_env="PLANE_TOKEN",
            workspace_slug="eng",
            project_id="proj-1",
        ),
        git=GitSettings(),
        kodo=KodoSettings(),
        repos={},
    )

    registry = CanonicalBackendRegistry.from_settings(settings)
    adapter = registry.for_backend(BackendName.AIDER_LOCAL)
    assert adapter is not None
    assert isinstance(adapter, AiderLocalBackendAdapter)
