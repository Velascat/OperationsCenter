import pytest
from pathlib import Path

from control_plane.adapters.kodo import KodoAdapter
from control_plane.config.settings import KodoSettings


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


def test_run_retries_with_claude_fallback_on_codex_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    goal_file = tmp_path / "goal.md"
    goal_file.write_text("## Goal\nDo something.\n")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)

        class FakeProc:
            returncode = 0
            stdout = ""
            stderr = ""

        if len(calls) == 1:
            FakeProc.returncode = 1
            FakeProc.stdout = "Error: 429 Too Many Requests from codex"
        return FakeProc()

    monkeypatch.setattr("subprocess.run", fake_run)

    adapter = KodoAdapter(KodoSettings())
    result = adapter.run(goal_file, repo_path)

    assert len(calls) == 2
    assert result.exit_code == 0
    # fallback team JSON should be cleaned up
    assert not (repo_path / ".kodo" / "team.json").exists()


def test_run_does_not_retry_on_non_quota_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    goal_file = tmp_path / "goal.md"
    goal_file.write_text("## Goal\nDo something.\n")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)

        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "some other error"

        return FakeProc()

    monkeypatch.setattr("subprocess.run", fake_run)

    adapter = KodoAdapter(KodoSettings())
    result = adapter.run(goal_file, repo_path)

    assert len(calls) == 1
    assert result.exit_code == 1
