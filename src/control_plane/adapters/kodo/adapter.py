from __future__ import annotations

import json
import subprocess
from pathlib import Path

from control_plane.config.settings import KodoSettings

# Strings in stdout/stderr that indicate the codex worker hit a quota/rate limit.
_CODEX_QUOTA_SIGNALS = (
    "429",
    "quota exceeded",
    "insufficient_quota",
    "rate limit exceeded",
    "too many requests",
)

# Strings that indicate the orchestrator (claude-code) has hit its usage limit.
_ORCHESTRATOR_RATE_LIMIT_SIGNALS = (
    "you've hit your limit",
    "you have hit your limit",
    "claude code error",
    "resets 2am",
    "usage limit reached",
)

# Fallback team used when codex quota is detected: claude CLI for both roles.
_CLAUDE_FALLBACK_TEAM = {
    "agents": {
        "worker_fast": {
            "backend": "claude",
            "model": "sonnet",
        },
        "worker_smart": {
            "backend": "claude",
            "model": "opus",
            "fallback_model": "sonnet",
        },
    }
}


class KodoRunResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str, command: list[str]) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.command = command


class KodoAdapter:
    def __init__(self, settings: KodoSettings) -> None:
        self.settings = settings

    def write_goal_file(self, path: Path, goal_text: str, constraints_text: str | None = None) -> Path:
        lines = ["## Goal", goal_text.strip()]
        if constraints_text:
            lines.extend(["", "## Constraints", constraints_text.strip()])
        path.write_text("\n".join(lines).strip() + "\n")
        return path

    def build_command(self, goal_file: Path, repo_path: Path) -> list[str]:
        return [
            self.settings.binary,
            "--goal-file",
            str(goal_file),
            "--project",
            str(repo_path),
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
        ]

    @staticmethod
    def _is_codex_quota_error(result: KodoRunResult) -> bool:
        combined = (result.stdout + result.stderr).lower()
        return any(signal in combined for signal in _CODEX_QUOTA_SIGNALS)

    @staticmethod
    def is_orchestrator_rate_limited(result: KodoRunResult) -> bool:
        combined = (result.stdout + result.stderr).lower()
        return any(signal in combined for signal in _ORCHESTRATOR_RATE_LIMIT_SIGNALS)

    def run(self, goal_file: Path, repo_path: Path, env: dict[str, str] | None = None) -> KodoRunResult:
        command = self.build_command(goal_file, repo_path)
        proc = subprocess.run(
            command,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=self.settings.timeout_seconds,
            check=False,
            env=env,
        )
        result = KodoRunResult(proc.returncode, proc.stdout, proc.stderr, command)

        if result.exit_code != 0 and self._is_codex_quota_error(result):
            result = self._run_with_claude_fallback(goal_file, repo_path, env=env)

        return result

    def _run_with_claude_fallback(self, goal_file: Path, repo_path: Path, env: dict[str, str] | None = None) -> KodoRunResult:
        team_override = repo_path / ".kodo" / "team.json"
        team_override.parent.mkdir(exist_ok=True)
        team_override.write_text(json.dumps(_CLAUDE_FALLBACK_TEAM, indent=2))
        try:
            command = self.build_command(goal_file, repo_path)
            proc = subprocess.run(
                command,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
                check=False,
                env=env,
            )
            return KodoRunResult(proc.returncode, proc.stdout, proc.stderr, command)
        finally:
            team_override.unlink(missing_ok=True)

    @staticmethod
    def command_to_json(command: list[str]) -> str:
        return json.dumps({"command": command}, indent=2)
