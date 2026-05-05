# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Kodo normalizer — Phase 2.

Maps raw Kodo capture into the normalized CxRP ExecutionResult shape.
Backend-specific data lives under ``evidence.extensions``; nothing
backend-specific leaks past this layer (enforced by the schema's
``additionalProperties: false`` and by ``test_normalizer_no_leakage``).
"""
from __future__ import annotations

import re
from typing import Any

from cxrp.contracts import Evidence, ExecutionResult
from cxrp.vocabulary.status import ExecutionStatus


class NormalizationError(ValueError):
    """Raised when raw Kodo output cannot be mapped to ExecutionResult."""


# G-003 fix (2026-05-05): Kodo can return exit_code=0 even when its
# internal stage execution crashed. Scan stdout for these markers and
# override status to FAILED.
_STDOUT_FAILURE_PATTERNS = (
    re.compile(r"Done:\s*0/\d+\s+stage(?:s)?\s+completed", re.IGNORECASE),
    re.compile(r"\bStage\s+\d+\s+\([^)]*\)\s+crashed", re.IGNORECASE),
    re.compile(r"\bcrashed:\s+\S", re.IGNORECASE),
    re.compile(r"\bStopping run\b", re.IGNORECASE),
)


def _status_from_exit(exit_code: int) -> ExecutionStatus:
    if exit_code == 0:
        return ExecutionStatus.SUCCEEDED
    return ExecutionStatus.FAILED


def _stdout_failure_reason(stdout: str) -> str | None:
    """Return a failure reason if stdout contains a Kodo internal-failure marker."""
    if not stdout:
        return None
    for pattern in _STDOUT_FAILURE_PATTERNS:
        match = pattern.search(stdout)
        if match:
            # Surface the matched line for the audit trail
            line_start = stdout.rfind("\n", 0, match.start()) + 1
            line_end = stdout.find("\n", match.end())
            line = stdout[line_start:line_end if line_end != -1 else None].strip()
            return f"internal stage failure: {line[:160]}"
    return None


def normalize(raw: dict[str, Any], *, request_id: str = "", result_id: str = "") -> ExecutionResult:
    """Normalize a raw Kodo capture into ExecutionResult.

    Expected keys in ``raw`` (all optional but typed):
      exit_code: int
      stdout, stderr: str
      files_changed: list[str]
      commands_run: list[str]
      tests_run: list[str]
      artifacts: list[str]
      Any other keys → evidence.extensions
    """
    if not isinstance(raw, dict):
        raise NormalizationError(f"raw must be a dict, got {type(raw).__name__}")

    exit_code = raw.get("exit_code", 0)
    if not isinstance(exit_code, int):
        raise NormalizationError(f"exit_code must be int, got {type(exit_code).__name__}")

    status = _status_from_exit(exit_code)
    stdout = raw.get("stdout") or ""

    # G-003: even with exit_code=0, scan stdout for stage failures.
    stdout_failure = _stdout_failure_reason(stdout)
    if status == ExecutionStatus.SUCCEEDED and stdout_failure is not None:
        status = ExecutionStatus.FAILED
    ok = status == ExecutionStatus.SUCCEEDED

    failure_reason: str | None = None
    if not ok:
        stderr = raw.get("stderr") or ""
        if stderr.strip():
            failure_reason = stderr.strip().splitlines()[-1]
        elif stdout_failure is not None:
            failure_reason = stdout_failure
        else:
            failure_reason = f"exit_code={exit_code}"

    known_keys = {"exit_code", "stdout", "stderr", "files_changed", "commands_run",
                  "tests_run", "artifacts", "summary"}
    extensions = {k: v for k, v in raw.items() if k not in known_keys}

    evidence = Evidence(
        files_changed=list(raw.get("files_changed", [])),
        commands_run=list(raw.get("commands_run", [])),
        tests_run=list(raw.get("tests_run", [])),
        artifacts_created=list(raw.get("artifacts", [])),
        failure_reason=failure_reason,
        extensions=extensions,
    )

    return ExecutionResult(
        result_id=result_id,
        request_id=request_id,
        ok=ok,
        status=status,
        summary=str(raw.get("summary", "")),
        evidence=evidence,
    )
