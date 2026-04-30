# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for observability/validation.py — normalize_validation."""

from __future__ import annotations

import pytest

from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ValidationStatus
from operations_center.observability.validation import normalize_validation


def _summary(
    status: ValidationStatus = ValidationStatus.SKIPPED,
    commands_run: int = 0,
    commands_passed: int = 0,
    commands_failed: int = 0,
    failure_excerpt: str | None = None,
) -> ValidationSummary:
    return ValidationSummary(
        status=status,
        commands_run=commands_run,
        commands_passed=commands_passed,
        commands_failed=commands_failed,
        failure_excerpt=failure_excerpt,
    )


# ---------------------------------------------------------------------------
# Status propagation
# ---------------------------------------------------------------------------


def test_passed_status_propagated():
    ev = normalize_validation(_summary(ValidationStatus.PASSED))
    assert ev.status == ValidationStatus.PASSED


def test_failed_status_propagated():
    ev = normalize_validation(_summary(ValidationStatus.FAILED))
    assert ev.status == ValidationStatus.FAILED


def test_skipped_status_propagated():
    ev = normalize_validation(_summary(ValidationStatus.SKIPPED))
    assert ev.status == ValidationStatus.SKIPPED


def test_error_status_propagated():
    ev = normalize_validation(_summary(ValidationStatus.ERROR))
    assert ev.status == ValidationStatus.ERROR


# ---------------------------------------------------------------------------
# Count propagation
# ---------------------------------------------------------------------------


def test_checks_run_propagated():
    ev = normalize_validation(_summary(commands_run=3))
    assert ev.checks_run == 3


def test_checks_passed_propagated():
    ev = normalize_validation(_summary(commands_passed=2))
    assert ev.checks_passed == 2


def test_checks_failed_propagated():
    ev = normalize_validation(_summary(commands_failed=1))
    assert ev.checks_failed == 1


def test_all_counts_propagated():
    ev = normalize_validation(_summary(
        commands_run=5,
        commands_passed=4,
        commands_failed=1,
    ))
    assert ev.checks_run == 5
    assert ev.checks_passed == 4
    assert ev.checks_failed == 1


# ---------------------------------------------------------------------------
# Failure excerpt / summary
# ---------------------------------------------------------------------------


def test_failure_excerpt_maps_to_summary():
    ev = normalize_validation(_summary(
        status=ValidationStatus.FAILED,
        failure_excerpt="ruff: 3 errors found in src/",
    ))
    assert ev.summary == "ruff: 3 errors found in src/"


def test_no_failure_excerpt_gives_none_summary():
    ev = normalize_validation(_summary(failure_excerpt=None))
    assert ev.summary is None


# ---------------------------------------------------------------------------
# artifact_refs defaults
# ---------------------------------------------------------------------------


def test_artifact_refs_default_empty():
    ev = normalize_validation(_summary())
    assert ev.artifact_refs == []


# ---------------------------------------------------------------------------
# Frozen model
# ---------------------------------------------------------------------------


def test_validation_evidence_is_frozen():
    ev = normalize_validation(_summary())
    with pytest.raises(Exception):
        ev.status = ValidationStatus.PASSED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Skipped = all zeros
# ---------------------------------------------------------------------------


def test_skipped_all_zero_counts():
    ev = normalize_validation(_summary(ValidationStatus.SKIPPED))
    assert ev.checks_run == 0
    assert ev.checks_passed == 0
    assert ev.checks_failed == 0
    assert ev.summary is None
