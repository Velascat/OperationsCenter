# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from operations_center.domain import ValidationResult


class ValidationRunner:
    def run(
        self,
        commands: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = 300,
    ) -> list[ValidationResult]:
        results: list[ValidationResult] = []
        for command in commands:
            start = time.monotonic()
            try:
                proc = subprocess.run(
                    command, cwd=cwd, shell=True, capture_output=True, text=True, env=env, check=False,
                    timeout=timeout_seconds,
                )
                duration_ms = int((time.monotonic() - start) * 1000)
                results.append(
                    ValidationResult(
                        command=command,
                        exit_code=proc.returncode,
                        stdout=proc.stdout,
                        stderr=proc.stderr,
                        duration_ms=duration_ms,
                    )
                )
            except subprocess.TimeoutExpired as exc:
                duration_ms = int((time.monotonic() - start) * 1000)
                raw_stdout = exc.stdout
                timeout_stdout = (
                    raw_stdout.decode(errors="replace") if isinstance(raw_stdout, bytes) else (raw_stdout or "")
                )
                results.append(
                    ValidationResult(
                        command=command,
                        exit_code=124,
                        stdout=timeout_stdout,
                        stderr=f"Command timed out after {timeout_seconds}s: {command}",
                        duration_ms=duration_ms,
                    )
                )
        return results

    @staticmethod
    def passed(results: list[ValidationResult]) -> bool:
        return all(r.exit_code == 0 for r in results)
