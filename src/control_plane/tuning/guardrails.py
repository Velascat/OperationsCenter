from __future__ import annotations

import os
from datetime import datetime, timedelta

from control_plane.tuning.models import (
    SkippedTuningChange,
    TuningChange,
    TuningRecommendation,
    TuningRunArtifact,
)

# Families the auto-apply path is allowed to touch.
AUTO_APPLY_FAMILIES: frozenset[str] = frozenset(
    {"observation_coverage", "test_visibility", "dependency_drift"}
)

# Config keys the auto-apply path is allowed to modify.
AUTO_APPLY_KEYS: frozenset[str] = frozenset({"min_consecutive_runs"})

# Actions that map to an auto-apply config change.
ACTIONABLE_ACTIONS: frozenset[str] = frozenset({"loosen_threshold", "tighten_threshold"})

# Hard min/max for min_consecutive_runs across all families.
MIN_CONSECUTIVE_RUNS_MIN = 1
MIN_CONSECUTIVE_RUNS_MAX = 5

_DEFAULT_MAX_CHANGES_PER_DAY = 2
_DEFAULT_FAMILY_COOLDOWN_HOURS = 48
_DEFAULT_MIN_SAMPLE_FOR_APPLY = 5


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


class TuningGuardrails:
    """Enforces safety limits for auto-apply tuning changes.

    Checks, in order:
    1. Family is in the allowlist.
    2. Action is actionable (loosen/tighten).
    3. Sufficient sample runs.
    4. The proposed new value is inside the allowed range.
    5. Per-family cooldown: no change to this family in the past N hours.
    6. Daily quota: no more than max_changes_per_day changes total today.
    7. No oscillation: do not reverse a change made in the cooldown window.
    """

    def __init__(
        self,
        *,
        max_changes_per_day: int | None = None,
        family_cooldown_hours: int | None = None,
        min_sample_for_apply: int | None = None,
    ) -> None:
        self.max_changes_per_day = max_changes_per_day or _env_int(
            "CONTROL_PLANE_TUNING_MAX_CHANGES_PER_DAY", _DEFAULT_MAX_CHANGES_PER_DAY
        )
        self.family_cooldown_hours = family_cooldown_hours or _env_int(
            "CONTROL_PLANE_TUNING_FAMILY_COOLDOWN_HOURS", _DEFAULT_FAMILY_COOLDOWN_HOURS
        )
        self.min_sample_for_apply = min_sample_for_apply or _DEFAULT_MIN_SAMPLE_FOR_APPLY

    def evaluate(
        self,
        recommendation: TuningRecommendation,
        current_value: int,
        prior_runs: list[TuningRunArtifact],
        changes_so_far: list[TuningChange],
        generated_at: datetime,
        sample_runs: int,
    ) -> tuple[bool, str]:
        """Returns (can_apply, skip_reason). skip_reason is empty string if can_apply."""
        family = recommendation.family
        action = recommendation.action

        if family not in AUTO_APPLY_FAMILIES:
            return False, "family_not_allowed"

        if action not in ACTIONABLE_ACTIONS:
            return False, f"action_not_applicable:{action}"

        if sample_runs < self.min_sample_for_apply:
            return False, "sample_too_small"

        new_value = _compute_new_value(current_value, action)
        if new_value is None:
            return False, "outside_range"

        # Per-family cooldown
        cooldown_cutoff = generated_at - timedelta(hours=self.family_cooldown_hours)
        for prior_run in prior_runs:
            for change in prior_run.changes_applied:
                if change.family == family and change.applied_at >= cooldown_cutoff:
                    return False, "cooldown_active"

        # Daily quota (including changes already applied in this run)
        today_start = generated_at.replace(hour=0, minute=0, second=0, microsecond=0)
        changes_today = sum(
            1
            for pr in prior_runs
            for ch in pr.changes_applied
            if ch.applied_at >= today_start
        ) + len(changes_so_far)
        if changes_today >= self.max_changes_per_day:
            return False, "quota_exceeded"

        # Oscillation check: don't reverse a recent change to this family
        for prior_run in prior_runs:
            for change in prior_run.changes_applied:
                if change.family == family and change.applied_at >= cooldown_cutoff:
                    prev_direction = "increase" if change.after > change.before else "decrease"
                    new_direction = "increase" if action == "tighten_threshold" else "decrease"
                    if prev_direction != new_direction:
                        return False, "oscillation_detected"

        return True, ""

    def build_skipped(
        self,
        recommendation: TuningRecommendation,
        reason: str,
        sample_runs: int,
    ) -> SkippedTuningChange:
        return SkippedTuningChange(
            family=recommendation.family,
            intended_action=recommendation.action,
            reason=reason,
            evidence={
                "family": recommendation.family,
                "action": recommendation.action,
                "sample_runs": sample_runs,
                **recommendation.evidence,
            },
        )


def _compute_new_value(current: int, action: str) -> int | None:
    if action == "loosen_threshold":
        new = current - 1
    elif action == "tighten_threshold":
        new = current + 1
    else:
        return None
    if MIN_CONSECUTIVE_RUNS_MIN <= new <= MIN_CONSECUTIVE_RUNS_MAX:
        return new
    return None


def compute_new_value(current: int, action: str) -> int | None:
    return _compute_new_value(current, action)
