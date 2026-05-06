# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
import pytest
from pathlib import Path

from operations_center.backends.kodo.runner import KodoAdapter
from operations_center.config.settings import KodoSettings


def test_build_command_uses_project_flag(tmp_path: Path) -> None:
    adapter = KodoAdapter(KodoSettings())

    command = adapter.build_command(tmp_path / "goal.md", tmp_path / "repo")

    assert "--project" in command
    project_index = command.index("--project")
    assert command[project_index + 1] == str(tmp_path / "repo")
    assert command[-1] == "--yes"


def test_build_command_respects_configured_binary(tmp_path: Path) -> None:
    adapter = KodoAdapter(KodoSettings(binary="scripts/kodo-shim"))

    command = adapter.build_command(tmp_path / "goal.md", tmp_path / "repo")

    assert command[0] == "scripts/kodo-shim"


def test_build_command_uses_configured_orchestrator(tmp_path: Path) -> None:
    adapter = KodoAdapter(KodoSettings(orchestrator="claude-code:opus"))

    command = adapter.build_command(tmp_path / "goal.md", tmp_path / "repo")

    idx = command.index("--orchestrator")
    assert command[idx + 1] == "claude-code:opus"


def test_run_returns_subprocess_result_unmodified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adapter.run() builds the command, runs once, returns the result.
    Backend-specific retry/fallback policy belongs in the OC backend layer.
    """
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    goal_file = tmp_path / "goal.md"
    goal_file.write_text("## Goal\nDo something.\n")

    calls: list[list[str]] = []

    class FakePopen:
        pid = 0
        returncode = 1

        def __init__(self, command, **kwargs):
            calls.append(command)

        def communicate(self, timeout=None):
            return ("", "some error")

    monkeypatch.setattr("subprocess.Popen", FakePopen)

    adapter = KodoAdapter(KodoSettings())
    result = adapter.run(goal_file, repo_path)

    assert len(calls) == 1
    assert result.exit_code == 1
    # No fallback team JSON ever created.
    assert not (repo_path / ".kodo" / "team.json").exists()
