from __future__ import annotations

import json
import os
import signal
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
# NOTE: do NOT add generic "claude code error" here — kodo emits
# "Claude Code error: None" as a recoverable status line that does not mean
# the usage limit was hit.  Only match phrases that unambiguously signal a
# rate/usage limit.
_ORCHESTRATOR_RATE_LIMIT_SIGNALS = (
    "you've hit your limit",
    "you have hit your limit",
    "resets 2am",
    "usage limit reached",
    "claude code error: you've hit",
    "claude code error: usage limit",
)

# Strings that indicate a hard quota exhaustion (account-level billing limit,
# not a transient rate limit).  These differ from _CODEX_QUOTA_SIGNALS which
# also match transient 429s — here we only match phrases that indicate the
# account has hit a non-recoverable limit that requires operator action.
_HARD_QUOTA_EXHAUSTED_SIGNALS = (
    "insufficient_quota",
    "you've exceeded your usage limit",
    "you have exceeded your usage limit",
    "you have run out of credits",
    "upgrade your plan",
    "billing",
    "payment required",
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

    def build_command(
        self,
        goal_file: Path,
        repo_path: Path,
        profile: "KodoSettings | None" = None,
        kodo_mode: str = "goal",
    ) -> list[str]:
        """Return the Kodo CLI command list.

        *profile* overrides individual fields from ``self.settings``.  Only
        fields present on a ``KodoSettings`` instance are considered; the
        binary is always taken from ``self.settings``.  *kodo_mode* selects
        the kodo invocation mode: "goal" (default), "test", or "improve".
        """
        s = self.settings
        base = [
            s.binary,
            "--goal-file",
            str(goal_file),
            "--project",
            str(repo_path),
            "--team",
            (profile.team if profile else s.team),
            "--cycles",
            str(profile.cycles if profile else s.cycles),
            "--exchanges",
            str(profile.exchanges if profile else s.exchanges),
            "--orchestrator",
            (profile.orchestrator if profile else s.orchestrator),
            "--effort",
            (profile.effort if profile else s.effort),
            "--yes",
        ]
        if kodo_mode == "test":
            return [s.binary, "--test"] + base[1:]
        if kodo_mode == "improve":
            return [s.binary, "--improve"] + base[1:]
        return base

    @staticmethod
    def _is_codex_quota_error(result: KodoRunResult) -> bool:
        combined = (result.stdout + result.stderr).lower()
        return any(signal in combined for signal in _CODEX_QUOTA_SIGNALS)

    @staticmethod
    def is_orchestrator_rate_limited(result: KodoRunResult) -> bool:
        combined = (result.stdout + result.stderr).lower()
        return any(signal in combined for signal in _ORCHESTRATOR_RATE_LIMIT_SIGNALS)

    @staticmethod
    def is_quota_exhausted(result: KodoRunResult) -> bool:
        """Return True when the result indicates a hard account-level quota exhaustion.

        Unlike transient rate limits (which the fallback path handles), quota
        exhaustion requires operator action (top up, upgrade plan, or wait for
        monthly reset).  Executions that hit quota exhaustion should NOT drain
        the circuit-breaker window — they are an infrastructure failure, not a
        task-quality failure.
        """
        combined = (result.stdout + result.stderr).lower()
        return any(signal in combined for signal in _HARD_QUOTA_EXHAUSTED_SIGNALS)

    @staticmethod
    def _run_subprocess(
        command: list[str],
        *,
        cwd: Path,
        timeout: int,
        env: dict[str, str] | None,
    ) -> KodoRunResult:
        """Run *command* in a new process session and return the result.

        Uses ``start_new_session=True`` so that the spawned process becomes the
        leader of a fresh process group.  On timeout, the **entire group** is
        killed via ``os.killpg`` — this reaps kodo subprocesses (e.g. Claude
        worker processes) that would otherwise become orphans and continue
        consuming CPU / API quota.
        """
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )

        # Capture the pgid immediately — used by both the timeout path and the
        # SIGTERM handler below.  pid==0 means the test used a fake Popen; skip.
        try:
            _pgid: int | None = os.getpgid(proc.pid) if proc.pid else None
        except OSError:
            _pgid = None

        def _kill_group() -> None:
            if _pgid is not None:
                try:
                    os.killpg(_pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass

        # Install a SIGTERM handler so that when the worker Python process is
        # killed (supervisor stop, OOM killer) the kodo process group — which
        # runs in its own session and would otherwise be orphaned — is also
        # killed before we exit.
        _prev_sigterm = signal.getsignal(signal.SIGTERM)

        def _sigterm_handler(signum: int, frame: object) -> None:
            _kill_group()
            # Restore the previous handler and re-raise so normal shutdown
            # (finally blocks, atexit, etc.) can still run.
            signal.signal(signal.SIGTERM, _prev_sigterm)
            os.kill(os.getpid(), signum)

        signal.signal(signal.SIGTERM, _sigterm_handler)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return KodoRunResult(proc.returncode, stdout, stderr, command)
        except subprocess.TimeoutExpired:
            # Kill the whole process group so no orphan sub-processes remain.
            _kill_group()
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            timeout_note = f"\n[timeout: process group killed after {timeout}s]"
            return KodoRunResult(-1, stdout or "", (stderr or "") + timeout_note, command)
        finally:
            signal.signal(signal.SIGTERM, _prev_sigterm)

    def run(
        self,
        goal_file: Path,
        repo_path: Path,
        env: dict[str, str] | None = None,
        profile: "KodoSettings | None" = None,
        kodo_mode: str = "goal",
    ) -> KodoRunResult:
        """Execute Kodo.  *profile* overrides individual settings fields."""
        timeout = (profile.timeout_seconds if profile else self.settings.timeout_seconds)
        command = self.build_command(goal_file, repo_path, profile=profile, kodo_mode=kodo_mode)
        result = self._run_subprocess(command, cwd=repo_path, timeout=timeout, env=env)

        if result.exit_code != 0 and self._is_codex_quota_error(result):
            result = self._run_with_claude_fallback(goal_file, repo_path, env=env, profile=profile, kodo_mode=kodo_mode)

        return result

    def _run_with_claude_fallback(
        self,
        goal_file: Path,
        repo_path: Path,
        env: dict[str, str] | None = None,
        profile: "KodoSettings | None" = None,
        kodo_mode: str = "goal",
    ) -> KodoRunResult:
        team_override = repo_path / ".kodo" / "team.json"
        team_override.parent.mkdir(exist_ok=True)
        team_override.write_text(json.dumps(_CLAUDE_FALLBACK_TEAM, indent=2))
        try:
            timeout = (profile.timeout_seconds if profile else self.settings.timeout_seconds)
            command = self.build_command(goal_file, repo_path, profile=profile, kodo_mode=kodo_mode)
            return self._run_subprocess(command, cwd=repo_path, timeout=timeout, env=env)
        finally:
            team_override.unlink(missing_ok=True)

    @staticmethod
    def command_to_json(command: list[str]) -> str:
        return json.dumps({"command": command}, indent=2)

    @staticmethod
    def get_version(binary: str) -> str | None:
        """Return the kodo binary version string, or None on failure.

        The result is intentionally not cached at the module level so that
        version-change detection (S6-8) works correctly across the lifetime of
        a long-running watcher process.  Callers should cache it themselves if
        they need to avoid repeated subprocess calls.
        """
        try:
            import subprocess as _sub
            result = _sub.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version = (result.stdout.strip() or result.stderr.strip())[:64]
            return version or None
        except Exception:
            return None
