from __future__ import annotations

import json
import subprocess
from pathlib import Path

from control_plane.config.settings import KodoSettings


class KodoRunResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str, command: list[str]) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.command = command


class KodoAdapter:
    def __init__(self, settings: KodoSettings) -> None:
        self.settings = settings

    def write_goal_file(self, path: Path, goal_text: str) -> Path:
        path.write_text(goal_text)
        return path

    def build_command(self, goal_file: Path, repo_path: Path) -> list[str]:
        return [
            self.settings.binary,
            "--goal-file",
            str(goal_file),
            "--team",
            self.settings.team,
            "--cycles",
            str(self.settings.cycles),
            "--exchanges",
            str(self.settings.exchanges),
            "--orchestrator",
            self.settings.orchestrator,
            "--effort",
            self.settings.effort,
            "--yes",
            str(repo_path),
        ]

    def run(self, goal_file: Path, repo_path: Path) -> KodoRunResult:
        command = self.build_command(goal_file, repo_path)
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.settings.timeout_seconds,
            check=False,
        )
        return KodoRunResult(proc.returncode, proc.stdout, proc.stderr, command)

    @staticmethod
    def command_to_json(command: list[str]) -> str:
        return json.dumps({"command": command}, indent=2)
