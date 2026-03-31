from __future__ import annotations

import subprocess
import time
from pathlib import Path

from control_plane.domain import ValidationResult


class ValidationRunner:
    def run(self, commands: list[str], cwd: Path, env: dict[str, str] | None = None) -> list[ValidationResult]:
        results: list[ValidationResult] = []
        for command in commands:
            start = time.monotonic()
            proc = subprocess.run(command, cwd=cwd, shell=True, capture_output=True, text=True, env=env, check=False)
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
        return results

    @staticmethod
    def passed(results: list[ValidationResult]) -> bool:
        return all(r.exit_code == 0 for r in results)
