# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
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


# Removed: test_run_returns_subprocess_result_unmodified — KodoAdapter.run()
# was deleted in Phase 3 cleanup. The actual subprocess execution lives in
# ExecutorRuntime now. Kodo backend wire-level coverage is in
# tests/unit/backends/kodo/test_invoke.py and test_rxp_wire.py.
