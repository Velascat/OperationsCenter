# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Validation profile constants and family-to-profile mapping.

A validation profile tells the execution worker what post-execution checks to run
in order to consider a task "done". The worker is expected to read this on a
best-effort basis; it is advisory, not a hard contract.

Profiles
--------
RUFF_CLEAN          lint_fix: `ruff check` must report zero violations after the change.
TY_CLEAN            type_fix: `ty check` / `mypy` must report zero errors after the change.
TESTS_PASS          Most logic/structural families: the full test suite must pass.
CI_GREEN            ci_pattern: identified failing or flaky CI checks must be resolved.
MANUAL_REVIEW       arch_promotion and tier-0 families: no automated validation gate;
                    human review is the acceptance criterion.
"""
from __future__ import annotations

RUFF_CLEAN = "ruff_clean"
TY_CLEAN = "ty_clean"
TESTS_PASS = "tests_pass"
CI_GREEN = "ci_green"
MANUAL_REVIEW = "manual_review"

_FAMILY_PROFILES: dict[str, str] = {
    "lint_fix": RUFF_CLEAN,
    "type_fix": TY_CLEAN,
    "test_visibility": TESTS_PASS,
    "execution_health_followup": TESTS_PASS,
    "observation_coverage": TESTS_PASS,
    "dependency_drift_followup": TESTS_PASS,
    "ci_pattern": CI_GREEN,
    "validation_pattern_followup": TESTS_PASS,
    "hotspot_concentration": TESTS_PASS,
    "todo_accumulation": TESTS_PASS,
    "backlog_promotion": TESTS_PASS,
    "arch_promotion": MANUAL_REVIEW,
}

_DEFAULT_PROFILE = TESTS_PASS


def profile_for_family(family: str) -> str:
    """Return the validation profile for the given candidate family.

    Falls back to TESTS_PASS for any family not in the table, so new families
    get a safe default without raising.
    """
    return _FAMILY_PROFILES.get(family, _DEFAULT_PROFILE)
