# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from pathlib import Path

from operations_center.adapters.workspace.bootstrap import RepoEnvironmentBootstrapper


def test_environment_for_prepends_repo_local_venv_bin() -> None:
    bootstrapper = RepoEnvironmentBootstrapper()
    repo_path = Path("/tmp/example-repo")

    env = bootstrapper.environment_for(repo_path, ".venv", base_env={"PATH": "/usr/bin"})

    assert env["VIRTUAL_ENV"] == "/tmp/example-repo/.venv"
    assert env["PATH"].startswith("/tmp/example-repo/.venv/bin:")
    assert env["PATH"].endswith("/usr/bin")


def test_prepare_uses_default_repo_relative_install_command(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []

    class FakeBootstrapper(RepoEnvironmentBootstrapper):
        def _run(self, command: list[str], *, cwd: Path, env: dict[str, str] | None):  # type: ignore[override]
            calls.append(("exec", command))
            venv_path = cwd / ".venv"
            (venv_path / "bin").mkdir(parents=True, exist_ok=True)
            return super()._run(["true"], cwd=cwd, env=env)

        def _run_shell(self, command: str, *, cwd: Path, env: dict[str, str] | None):  # type: ignore[override]
            calls.append(("shell", command))
            return super()._run(["true"], cwd=cwd, env=env)

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    bootstrapper = FakeBootstrapper()
    bootstrapper.prepare(
        repo_path,
        python_binary="python3",
        venv_dir=".venv",
        install_dev_command=None,
        base_env={"PATH": "/usr/bin"},
    )

    assert calls[0] == ("exec", ["python3", "-m", "venv", ".venv"])
    assert calls[1] == ("exec", [str(repo_path / ".venv" / "bin" / "python"), "-m", "pip", "install", "--upgrade", "pip"])
    assert calls[2] == ("shell", f"{repo_path / '.venv' / 'bin' / 'pip'} install -e .[dev]")


def test_prepare_disabled_leaves_environment_unmodified(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    bootstrapper = RepoEnvironmentBootstrapper()

    result = bootstrapper.prepare(
        repo_path,
        python_binary="python3",
        venv_dir=".venv",
        install_dev_command=None,
        base_env={"PATH": "/usr/bin"},
        enabled=False,
    )

    assert result.commands == []
    assert result.env == {"PATH": "/usr/bin"}
