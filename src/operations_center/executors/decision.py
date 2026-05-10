# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Phase 11 — mechanical decision matrix.

Computes the expected ``outcome`` from the per-phase classifications.
Used to (a) verify that a hand-written ``audit_verdict.yaml`` is
internally consistent, and (b) suggest the outcome during audit.

  All PASS / N/A         → adapter_only
  Any PARTIAL, no FAIL   → adapter_plus_wrapper
  Any FAIL               → upstream_patch_pending OR fork_required

The fork-vs-upstream split needs human judgment + the upstream-patch
criteria from Phase 11 (PR merge time, maintainer responsiveness, fork
deadline). This module returns ``UPSTREAM_PATCH_PENDING_OR_FORK`` for
the FAIL case rather than guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from operations_center.executors._artifacts import (
    AuditOutcome,
    AuditVerdict,
    PhaseClassification,
)


class ExpectedOutcome(str, Enum):
    ADAPTER_ONLY                    = "adapter_only"
    ADAPTER_PLUS_WRAPPER            = "adapter_plus_wrapper"
    UPSTREAM_PATCH_PENDING_OR_FORK  = "upstream_patch_pending_or_fork"


@dataclass(frozen=True)
class DecisionResult:
    expected: ExpectedOutcome
    has_fail: bool
    has_partial: bool
    fail_phases: tuple[str, ...]
    partial_phases: tuple[str, ...]


def compute_expected_outcome(per_phase: Mapping[str, PhaseClassification]) -> DecisionResult:
    fails = tuple(p for p, c in per_phase.items() if c == PhaseClassification.FAIL)
    parts = tuple(p for p, c in per_phase.items() if c == PhaseClassification.PARTIAL)
    if fails:
        expected = ExpectedOutcome.UPSTREAM_PATCH_PENDING_OR_FORK
    elif parts:
        expected = ExpectedOutcome.ADAPTER_PLUS_WRAPPER
    else:
        # All PASS or N/A — N/A counts as PASS in the matrix.
        expected = ExpectedOutcome.ADAPTER_ONLY
    return DecisionResult(
        expected=expected,
        has_fail=bool(fails),
        has_partial=bool(parts),
        fail_phases=fails,
        partial_phases=parts,
    )


def verdict_is_consistent(verdict: AuditVerdict) -> tuple[bool, str]:
    """Returns (ok, reason). ok=True iff verdict.outcome matches the matrix."""
    decision = compute_expected_outcome(verdict.per_phase)
    actual = verdict.outcome
    if decision.expected == ExpectedOutcome.ADAPTER_ONLY:
        if actual == AuditOutcome.ADAPTER_ONLY:
            return True, ""
        return False, f"all phases PASS/N/A but outcome is {actual.value!r}, expected adapter_only"
    if decision.expected == ExpectedOutcome.ADAPTER_PLUS_WRAPPER:
        if actual == AuditOutcome.ADAPTER_PLUS_WRAPPER:
            return True, ""
        return False, (
            f"PARTIAL phases {decision.partial_phases} but outcome is "
            f"{actual.value!r}, expected adapter_plus_wrapper"
        )
    # FAIL → either upstream_patch_pending or fork_required
    if actual in (AuditOutcome.UPSTREAM_PATCH_PENDING, AuditOutcome.FORK_REQUIRED):
        return True, ""
    return False, (
        f"FAIL phases {decision.fail_phases} but outcome is {actual.value!r}, "
        "expected upstream_patch_pending or fork_required"
    )
