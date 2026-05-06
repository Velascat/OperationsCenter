# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Kodo binary runner — raw subprocess layer.

``KodoAdapter`` builds the kodo command, runs it as a process group, and
returns a ``KodoRunResult`` (exit code + stdout + stderr + command).
This is the lower layer the backend's ``KodoBackendInvoker`` consumes.

Generic subprocess mechanics (``_run_subprocess``) are still local to
this module pending Phase 3 extraction into ExecutorRuntime.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import NoReturn

from operations_center.config.settings import KodoSettings


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
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
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

        _prev_sigterm = signal.getsignal(signal.SIGTERM)

        def _sigterm_handler(signum: int, _frame: object) -> NoReturn:
            _kill_group()
            signal.signal(signal.SIGTERM, _prev_sigterm)
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, _sigterm_handler)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return KodoRunResult(proc.returncode, stdout, stderr, command)
        except subprocess.TimeoutExpired:
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
        """Execute Kodo. *profile* overrides individual settings fields.

        Build command, run once, return result. Backend-specific retry/
        fallback policy belongs in the OC backend layer, not here.
        """
        timeout = (profile.timeout_seconds if profile else self.settings.timeout_seconds)
        command = self.build_command(goal_file, repo_path, profile=profile, kodo_mode=kodo_mode)
        return self._run_subprocess(command, cwd=repo_path, timeout=timeout, env=env)

    @staticmethod
    def command_to_json(command: list[str]) -> str:
        return json.dumps({"command": command}, indent=2, ensure_ascii=False)

    @staticmethod
    def get_version(binary: str) -> str | None:
        """Return the kodo binary version string, or None on failure.

        The result is intentionally not cached at the module level so that
        version-change detection (S6-8) works correctly across the lifetime of
        a long-running watcher process.  Callers should cache it themselves if
        they need to avoid repeated subprocess calls.
        """
        try:
            result = subprocess.run(
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
