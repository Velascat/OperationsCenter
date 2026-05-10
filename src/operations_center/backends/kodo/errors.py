# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/kodo/errors.py — error category mapping for the kodo backend adapter.

Maps kodo-specific failure signals into canonical FailureReasonCategory values
and structured failure messages.
"""

from __future__ import annotations

from operations_center.contracts.enums import FailureReasonCategory


# ---------------------------------------------------------------------------
# Signal strings used to classify kodo failure output
# ---------------------------------------------------------------------------

_TIMEOUT_SIGNALS = ("[timeout:", "timeout expired", "process group killed")

_QUOTA_SIGNALS = (
    "429",
    "quota exceeded",
    "insufficient_quota",
    "rate limit exceeded",
    "too many requests",
    "you've hit your limit",
    "usage limit reached",
    "you have run out of credits",
    "payment required",
)

_NO_CHANGES_SIGNALS = (
    "no changes",
    "nothing to commit",
    "nothing changed",
    "no diff",
    "working tree clean",
)

_CONFLICT_SIGNALS = (
    "merge conflict",
    "conflict",
    "cannot merge",
    "auto-merge failed",
)


def categorize_failure(exit_code: int, combined_output: str) -> FailureReasonCategory:
    """Return the best-fit FailureReasonCategory for a failed kodo run."""
    lower = combined_output.lower()

    if any(s in lower for s in _TIMEOUT_SIGNALS):
        return FailureReasonCategory.TIMEOUT

    if any(s in lower for s in _QUOTA_SIGNALS):
        return FailureReasonCategory.BACKEND_ERROR

    if any(s in lower for s in _NO_CHANGES_SIGNALS):
        return FailureReasonCategory.NO_CHANGES

    if any(s in lower for s in _CONFLICT_SIGNALS):
        return FailureReasonCategory.CONFLICT

    if exit_code == 0:
        # exit 0 but flagged as failure — normalization-level issue
        return FailureReasonCategory.UNKNOWN

    return FailureReasonCategory.BACKEND_ERROR


def build_failure_reason(exit_code: int, stderr: str, stdout: str) -> str:
    """Return a concise human-readable failure reason."""
    combined = (stderr or "").strip() or (stdout or "").strip()
    excerpt = combined[:300].replace("\n", " ").strip()
    if not excerpt:
        return f"kodo exited with code {exit_code}"
    return f"kodo exited {exit_code}: {excerpt}"
