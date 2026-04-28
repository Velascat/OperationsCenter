from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

from operations_center.config.settings import KodoSettings

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
# Sonnet is the workhorse here — fast for routine work and the smart-worker
# fallback when Opus is briefly throttled.
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

# Second-tier fallback used when Sonnet's weekly usage is specifically
# exhausted on Claude Pro/Max plans. Opus and Haiku each carry their own
# weekly budget separate from Sonnet, so this team can keep the pipeline
# moving even when Sonnet is fully consumed for the week. Haiku takes the
# fast role (where Sonnet was) and is also Opus's fallback in case Opus
# briefly throttles within its own budget.
_OPUS_HAIKU_FALLBACK_TEAM = {
    "agents": {
        "worker_fast": {
            "backend": "claude",
            "model": "haiku",
        },
        "worker_smart": {
            "backend": "claude",
            "model": "opus",
            "fallback_model": "haiku",
        },
    }
}

# Phrases that suggest Claude *Sonnet* specifically has hit its weekly cap.
# Pro/Max plans surface model-tier limits separately from account-level
# quota, so a Sonnet-only signal lets us route to Opus+Haiku without
# treating it as account-wide exhaustion (which would correctly stop work).
_SONNET_EXHAUSTED_SIGNALS = (
    "sonnet usage limit",
    "sonnet rate limit",
    "sonnet weekly",
    "claude sonnet limit",
    "claude-3-5-sonnet usage",
    "claude-3-5-sonnet rate limit",
)


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
        lines.extend([
            "",
            "## Commit message",
            "Write a descriptive conventional-commit message explaining WHAT changed and WHY.",
            "Format: `<type>(<scope>): <short summary>`",
            "Follow with a blank line and 1-3 sentences of body context (motivation, approach, trade-offs).",
            "Do NOT use generic messages like 'apply task' or include task IDs in the subject line.",
        ])
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
        # --goal-file, --test, and --improve are mutually exclusive in kodo's
        # argparse (same group).  Build the shared tail without any mode flag,
        # then prepend the correct mode flag per kodo_mode.
        tail = [
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
            return [s.binary, "--test"] + tail
        if kodo_mode == "improve":
            return [s.binary, "--improve"] + tail
        return [s.binary, "--goal-file", str(goal_file)] + tail

    @staticmethod
    def _is_codex_quota_error(result: KodoRunResult) -> bool:
        combined = (result.stdout + result.stderr).lower()
        return any(signal in combined for signal in _CODEX_QUOTA_SIGNALS)

    @staticmethod
    def is_orchestrator_rate_limited(result: KodoRunResult) -> bool:
        combined = (result.stdout + result.stderr).lower()
        return any(signal in combined for signal in _ORCHESTRATOR_RATE_LIMIT_SIGNALS)

    @staticmethod
    def _is_sonnet_exhausted(result: KodoRunResult) -> bool:
        """Return True when the result signals Sonnet (and only Sonnet) is out.

        Used to choose between two claude-team variants: when this fires we
        retry with Opus+Haiku, because those tiers have separate weekly
        budgets on Pro/Max plans and may still have headroom.
        """
        combined = (result.stdout + result.stderr).lower()
        return any(signal in combined for signal in _SONNET_EXHAUSTED_SIGNALS)

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
            # Restore the previous handler.  We raise SystemExit rather than
            # re-sending the signal via os.kill: re-sending with SIG_DFL causes
            # the OS to terminate the process immediately, bypassing Python's
            # finally blocks (including workspace cleanup in service.py).
            # SystemExit is a BaseException so it is NOT caught by bare
            # "except Exception" handlers — it unwinds the stack normally,
            # runs every finally clause, then exits.  Exit code 128+SIGTERM
            # (143) follows the shell convention for signal-terminated processes.
            signal.signal(signal.SIGTERM, _prev_sigterm)
            raise SystemExit(128 + signum)

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

        # Tier 1 fallback: codex quota exhausted → claude (Sonnet primary).
        if result.exit_code != 0 and self._is_codex_quota_error(result):
            result = self._run_with_team(
                _CLAUDE_FALLBACK_TEAM,
                goal_file, repo_path, env=env, profile=profile, kodo_mode=kodo_mode,
            )

        # Tier 2 fallback: Sonnet weekly limit hit (or generic claude rate
        # limit during a Sonnet-using run) → retry with Opus+Haiku. Those
        # tiers have separate weekly budgets on Pro/Max plans and often
        # still have headroom when Sonnet is exhausted.
        if result.exit_code != 0 and (
            self._is_sonnet_exhausted(result)
            or self.is_orchestrator_rate_limited(result)
        ):
            result = self._run_with_team(
                _OPUS_HAIKU_FALLBACK_TEAM,
                goal_file, repo_path, env=env, profile=profile, kodo_mode=kodo_mode,
            )

        return result

    def _run_with_team(
        self,
        team: dict,
        goal_file: Path,
        repo_path: Path,
        env: dict[str, str] | None = None,
        profile: "KodoSettings | None" = None,
        kodo_mode: str = "goal",
    ) -> KodoRunResult:
        """Run kodo with a team override written to .kodo/team.json."""
        team_override = repo_path / ".kodo" / "team.json"
        team_override.parent.mkdir(exist_ok=True)
        team_override.write_text(json.dumps(team, indent=2))
        try:
            timeout = (profile.timeout_seconds if profile else self.settings.timeout_seconds)
            command = self.build_command(goal_file, repo_path, profile=profile, kodo_mode=kodo_mode)
            return self._run_subprocess(command, cwd=repo_path, timeout=timeout, env=env)
        finally:
            team_override.unlink(missing_ok=True)

    # Back-compat shim — older call sites still reference this name.
    def _run_with_claude_fallback(
        self,
        goal_file: Path,
        repo_path: Path,
        env: dict[str, str] | None = None,
        profile: "KodoSettings | None" = None,
        kodo_mode: str = "goal",
    ) -> KodoRunResult:
        return self._run_with_team(
            _CLAUDE_FALLBACK_TEAM,
            goal_file, repo_path, env=env, profile=profile, kodo_mode=kodo_mode,
        )

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


# ── module-level helpers (cited in autonomy_gaps.md) ─────────────────────────

def _get_kodo_version(binary: str | None = None) -> str | None:
    """Return the kodo binary version string, or None on failure.

    Module-level shim around ``KodoAdapter.get_version`` so callers outside
    an adapter instance (e.g. capture writers, observability collectors) can
    record the version that produced an execution. No side effects.

    See `docs/design/autonomy_gaps.md` S6-8 (Kodo Version Attribution).
    """
    if binary is None:
        binary = "kodo"
    return KodoAdapter.get_version(binary)


def _is_quota_exhausted_result(result: KodoRunResult) -> bool:
    """Return True when a kodo run signals account-level quota exhaustion.

    Module-level shim around ``KodoAdapter.is_quota_exhausted`` so non-
    adapter callers (the board_worker, the observability layer) can decide
    whether to circuit-break further runs. Hard quota differs from
    transient rate limits — see ``_HARD_QUOTA_EXHAUSTED_SIGNALS``.

    See `docs/design/autonomy_gaps.md` S5-5 (Kodo API Quota Exhaustion
    Detection).
    """
    return KodoAdapter.is_quota_exhausted(result)
