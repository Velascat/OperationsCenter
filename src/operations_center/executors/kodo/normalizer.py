# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Kodo normalizer — Phase 2.

Maps raw Kodo capture into the normalized CxRP ExecutionResult shape.
Backend-specific data lives under ``evidence.extensions``; nothing
backend-specific leaks past this layer (enforced by the schema's
``additionalProperties: false`` and by ``test_normalizer_no_leakage``).
"""
from __future__ import annotations

from typing import Any

from cxrp.contracts import Evidence, ExecutionResult
from cxrp.vocabulary.status import ExecutionStatus


class NormalizationError(ValueError):
    """Raised when raw Kodo output cannot be mapped to ExecutionResult."""


def _status_from_exit(exit_code: int) -> ExecutionStatus:
    if exit_code == 0:
        return ExecutionStatus.SUCCEEDED
    return ExecutionStatus.FAILED


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
    ok = status == ExecutionStatus.SUCCEEDED

    failure_reason: str | None = None
    if not ok:
        stderr = raw.get("stderr") or ""
        failure_reason = stderr.strip().splitlines()[-1] if stderr.strip() else f"exit_code={exit_code}"

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
