# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Execute managed audit commands as external subprocesses.

Captures stdout and stderr to log files. Never uses shell=True.

The command string from ManagedAuditInvocationRequest is split with
shlex.split() to produce a safe argument list for Popen.
"""

from __future__ import annotations

import os
import platform
import shlex
import signal
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from operations_center.audit_toolset import ManagedAuditInvocationRequest


@dataclass
class ProcessResult:
    """Outcome of a managed audit subprocess execution."""

    exit_code: int | None
    stdout_path: Path
    stderr_path: Path
    started_at: datetime
    ended_at: datetime
    timed_out: bool = False
    error: str | None = None

    @property
    def duration_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None


def _now_utc() -> datetime:
    return datetime.now(UTC)


class ManagedAuditExecutor:
    """Execute a managed audit command and capture stdout/stderr to log files.

    Parameters
    ----------
    log_dir:
        Directory for stdout.log and stderr.log. Created if absent.
    """

    def __init__(self, log_dir: Path | str) -> None:
        self._log_dir = Path(log_dir)

    def execute(
        self,
        request: ManagedAuditInvocationRequest,
        *,
        timeout_seconds: float | None = None,
        cwd_override: str | None = None,
    ) -> ProcessResult:
        """Execute the managed audit command.

        Parameters
        ----------
        request:
            Validated Phase 3 invocation request.
        timeout_seconds:
            Hard wall-clock timeout in seconds. None = no timeout.
        cwd_override:
            Override working directory (must be absolute). When None, uses
            request.working_directory.

        Returns
        -------
        ProcessResult
            Always returned — subprocess failures produce a result, not an
            exception.  The caller inspects exit_code, timed_out, and error.
        """
        self._log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = self._log_dir / "stdout.log"
        stderr_path = self._log_dir / "stderr.log"

        cwd = cwd_override or request.working_directory
        args = shlex.split(request.command)
        env = dict(request.env)

        started_at = _now_utc()

        popen_kwargs: dict = {"cwd": cwd, "env": env}
        if platform.system() != "Windows":
            popen_kwargs["preexec_fn"] = os.setsid  # process group for clean timeout

        try:
            with (
                open(stdout_path, "w", encoding="utf-8", errors="replace") as out_f,
                open(stderr_path, "w", encoding="utf-8", errors="replace") as err_f,
            ):
                proc = subprocess.Popen(args, stdout=out_f, stderr=err_f, **popen_kwargs)
                try:
                    proc.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    _terminate_process(proc)
                    ended_at = _now_utc()
                    return ProcessResult(
                        exit_code=proc.returncode,
                        stdout_path=stdout_path,
                        stderr_path=stderr_path,
                        started_at=started_at,
                        ended_at=ended_at,
                        timed_out=True,
                        error=(
                            f"audit timed out after {timeout_seconds}s "
                            f"(command: {request.command!r})"
                        ),
                    )
        except Exception as exc:
            ended_at = _now_utc()
            return ProcessResult(
                exit_code=None,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                started_at=started_at,
                ended_at=ended_at,
                error=f"executor error launching command {request.command!r}: {exc!r}",
            )

        ended_at = _now_utc()
        return ProcessResult(
            exit_code=proc.returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            started_at=started_at,
            ended_at=ended_at,
        )


def _terminate_process(proc: subprocess.Popen) -> None:
    """Send SIGTERM to the process group, escalate to SIGKILL if unresponsive."""
    try:
        if platform.system() != "Windows":
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if platform.system() != "Windows":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
            proc.wait()
    except ProcessLookupError:
        pass  # process already exited
