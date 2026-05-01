# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/archon/errors.py — error category mapping for the Archon backend adapter.

Maps Archon-specific failure signals into canonical FailureReasonCategory values
and structured failure messages. Archon may surface workflow-specific outcomes
(partial completion, workflow aborted) that kodo does not produce.
"""

from __future__ import annotations

from operations_center.contracts.enums import FailureReasonCategory


_TIMEOUT_SIGNALS = (
    "[timeout:",
    "timeout expired",
    "process killed",
    "deadline exceeded",
    "workflow timed out",
)

_NO_CHANGES_SIGNALS = (
    "no changes",
    "nothing to commit",
    "nothing changed",
    "no diff",
    "working tree clean",
    "no modifications",
)

_CONFLICT_SIGNALS = (
    "merge conflict",
    "conflict",
    "cannot merge",
    "auto-merge failed",
)

_VALIDATION_FAILED_SIGNALS = (
    "validation failed",
    "validation error",
    "checks failed",
    "linting failed",
    "test failures",
)


def categorize_failure(
    outcome: str,
    combined_output: str,
) -> FailureReasonCategory:
    """Return the best-fit FailureReasonCategory for a failed Archon run."""
    if outcome == "timeout":
        return FailureReasonCategory.TIMEOUT

    lower = combined_output.lower()

    if any(s in lower for s in _TIMEOUT_SIGNALS):
        return FailureReasonCategory.TIMEOUT

    if any(s in lower for s in _NO_CHANGES_SIGNALS):
        return FailureReasonCategory.NO_CHANGES

    if any(s in lower for s in _CONFLICT_SIGNALS):
        return FailureReasonCategory.CONFLICT

    if any(s in lower for s in _VALIDATION_FAILED_SIGNALS):
        return FailureReasonCategory.VALIDATION_FAILED

    return FailureReasonCategory.BACKEND_ERROR


def build_failure_reason(outcome: str, error_text: str, output_text: str) -> str:
    """Return a concise human-readable failure reason."""
    combined = (error_text or "").strip() or (output_text or "").strip()
    excerpt = combined[:300].replace("\n", " ").strip()
    if outcome == "timeout":
        return "archon workflow timed out"
    if outcome == "partial":
        prefix = "archon workflow completed partially"
        return f"{prefix}: {excerpt}" if excerpt else prefix
    if not excerpt:
        return f"archon workflow failed (outcome={outcome})"
    return f"archon failed: {excerpt}"
