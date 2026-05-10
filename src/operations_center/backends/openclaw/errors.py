# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/openclaw/errors.py — error category mapping for the OpenClaw backend adapter.

Maps OpenClaw-specific failure signals into canonical FailureReasonCategory values
and structured failure messages. OpenClaw may surface agent-specific outcomes
(partial completion, context limit, tool failure) that differ from kodo.
"""

from __future__ import annotations

from operations_center.contracts.enums import FailureReasonCategory


_TIMEOUT_SIGNALS = (
    "[timeout:",
    "timeout expired",
    "process killed",
    "deadline exceeded",
    "execution timed out",
    "max tokens reached",
    "context window exceeded",
)

_NO_CHANGES_SIGNALS = (
    "no changes",
    "nothing to commit",
    "nothing changed",
    "no diff",
    "working tree clean",
    "no modifications needed",
    "already up to date",
)

_CONFLICT_SIGNALS = (
    "merge conflict",
    "conflict",
    "cannot merge",
    "auto-merge failed",
    "rebase conflict",
)

_VALIDATION_FAILED_SIGNALS = (
    "validation failed",
    "validation error",
    "checks failed",
    "linting failed",
    "test failures",
    "tests failed",
    "type errors found",
)

def categorize_failure(
    outcome: str,
    combined_output: str,
) -> FailureReasonCategory:
    """Return the best-fit FailureReasonCategory for a failed OpenClaw run."""
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
        return "openclaw execution timed out"
    if outcome == "partial":
        prefix = "openclaw completed partially"
        return f"{prefix}: {excerpt}" if excerpt else prefix
    if not excerpt:
        return f"openclaw failed (outcome={outcome})"
    return f"openclaw failed: {excerpt}"
