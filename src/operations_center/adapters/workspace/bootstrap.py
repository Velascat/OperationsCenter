from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BootstrapCommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


@dataclass
class BootstrapResult:
    venv_path: Path
    env: dict[str, str]
    commands: list[BootstrapCommandResult]


class RepoEnvironmentBootstrapper:
    def prepare(
        self,
        repo_path: Path,
        *,
        python_binary: str,
        venv_dir: str,
        install_dev_command: str | None,
        base_env: dict[str, str] | None = None,
        enabled: bool = True,
        bootstrap_commands: list[str] | None = None,
    ) -> BootstrapResult:
        venv_path = repo_path / venv_dir
        commands: list[BootstrapCommandResult] = []
        base = dict(base_env or {})

        if not enabled:
            # Auto-discover a repo-provided bootstrap script when no explicit commands
            # are configured.  Convention: repos place their primary environment
            # bootstrap at tools/bootstrap.sh.  OperationsCenter runs it when present.
            if bootstrap_commands is None:
                candidate = repo_path / "tools" / "bootstrap.sh"
                if candidate.exists():
                    bootstrap_commands = ["bash tools/bootstrap.sh"]
            if bootstrap_commands:
                for cmd in bootstrap_commands:
                    commands.append(self._run_shell(cmd, cwd=repo_path, env=base))
                # Return the venv env so callers (kodo, validation) have VIRTUAL_ENV
                # and PATH pointing at the bootstrapped venv.
                env = self.environment_for(repo_path, venv_dir, base_env=base)
                return BootstrapResult(venv_path=venv_path, env=env, commands=commands)
            return BootstrapResult(venv_path=venv_path, env=base, commands=commands)

        # Custom bootstrap commands override Python venv setup (for non-Python repos)
        if bootstrap_commands:
            for cmd in bootstrap_commands:
                commands.append(self._run_shell(cmd, cwd=repo_path, env=base))
            return BootstrapResult(venv_path=venv_path, env=base, commands=commands)

        env = self.environment_for(repo_path, venv_dir, base_env=base_env)
        if not venv_path.exists():
            commands.append(self._run([python_binary, "-m", "venv", venv_dir], cwd=repo_path, env=base_env))

        venv_python = self.python_path(repo_path, venv_dir)
        commands.append(
            self._run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], cwd=repo_path, env=env)
        )

        install_command = install_dev_command or f"{self.bin_path(repo_path, venv_dir, 'pip')} install -e .[dev]"
        commands.append(self._run_shell(install_command, cwd=repo_path, env=env))
        return BootstrapResult(venv_path=venv_path, env=env, commands=commands)

    def environment_for(
        self,
        repo_path: Path,
        venv_dir: str,
        *,
        base_env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        env = dict(base_env or {})
        venv_path = repo_path / venv_dir
        bin_dir = self.bin_dir(repo_path, venv_dir)
        existing_path = env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(venv_path)
        env["PATH"] = f"{bin_dir}:{existing_path}" if existing_path else str(bin_dir)
        return env

    def python_path(self, repo_path: Path, venv_dir: str) -> Path:
        return self.bin_dir(repo_path, venv_dir) / "python"

    def bin_path(self, repo_path: Path, venv_dir: str, binary_name: str) -> Path:
        return self.bin_dir(repo_path, venv_dir) / binary_name

    def bin_dir(self, repo_path: Path, venv_dir: str) -> Path:
        return repo_path / venv_dir / "bin"

    def _run(self, command: list[str], *, cwd: Path, env: dict[str, str] | None) -> BootstrapCommandResult:
        start = time.monotonic()
        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, env=env, check=False)
        duration_ms = int((time.monotonic() - start) * 1000)
        if proc.returncode != 0:
            raise RuntimeError(f"bootstrap command failed: {' '.join(command)}\n{proc.stderr}")
        return BootstrapCommandResult(
            command=command,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_ms=duration_ms,
        )

    def _run_shell(self, command: str, *, cwd: Path, env: dict[str, str] | None) -> BootstrapCommandResult:
        start = time.monotonic()
        proc = subprocess.run(command, cwd=cwd, shell=True, capture_output=True, text=True, env=env, check=False)
        duration_ms = int((time.monotonic() - start) * 1000)
        if proc.returncode != 0:
            raise RuntimeError(f"bootstrap command failed: {command}\n{proc.stderr}")
        return BootstrapCommandResult(
            command=["sh", "-lc", command],
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_ms=duration_ms,
        )
