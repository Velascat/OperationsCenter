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
