# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from operations_center.tuning.guardrails import TuningGuardrails, compute_new_value
from operations_center.tuning.models import TuningChange, TuningRecommendation, TuningRunArtifact

_NOW = datetime(2026, 4, 4, 12, tzinfo=UTC)


def _rec(family: str = "observation_coverage", action: str = "loosen_threshold") -> TuningRecommendation:
    return TuningRecommendation(
        family=family,
        action=action,
        rationale="test",
        confidence="high",
        evidence={"sample_runs": 10},
    )


def _prior_run_with_change(family: str, key: str, before: int, after: int, applied_at: datetime) -> TuningRunArtifact:
    return TuningRunArtifact(
        run_id="tun_prior",
        generated_at=applied_at,
        source_command="test",
        window_runs=10,
        changes_applied=[TuningChange(family=family, key=key, before=before, after=after, reason="test", applied_at=applied_at)],
    )


guards = TuningGuardrails(max_changes_per_day=2, family_cooldown_hours=48, min_sample_for_apply=5)


# --- Allowlist ---

def test_rejects_family_not_in_allowlist() -> None:
    can, reason = guards.evaluate(_rec("hotspot_concentration"), 2, [], [], _NOW, 10)
    assert not can
    assert reason == "family_not_allowed"


def test_rejects_todo_accumulation_family() -> None:
    can, reason = guards.evaluate(_rec("todo_accumulation"), 2, [], [], _NOW, 10)
    assert not can
    assert reason == "family_not_allowed"


# --- Actionable actions ---

def test_rejects_keep_action() -> None:
    can, reason = guards.evaluate(_rec(action="keep"), 2, [], [], _NOW, 10)
    assert not can
    assert "action_not_applicable" in reason


def test_rejects_review_action() -> None:
    can, reason = guards.evaluate(_rec(action="review"), 2, [], [], _NOW, 10)
    assert not can


# --- Sample size ---

def test_rejects_when_sample_too_small() -> None:
    can, reason = guards.evaluate(_rec(), 2, [], [], _NOW, sample_runs=3)
    assert not can
    assert reason == "sample_too_small"


# --- Range limits ---

def test_rejects_loosen_when_already_at_minimum() -> None:
    # current=1, loosen would go to 0 which is below MIN=1
    can, reason = guards.evaluate(_rec(action="loosen_threshold"), current_value=1, prior_runs=[], changes_so_far=[], generated_at=_NOW, sample_runs=10)
    assert not can
    assert reason == "outside_range"


def test_rejects_tighten_when_already_at_maximum() -> None:
    can, reason = guards.evaluate(_rec(action="tighten_threshold"), current_value=5, prior_runs=[], changes_so_far=[], generated_at=_NOW, sample_runs=10)
    assert not can
    assert reason == "outside_range"


# --- Cooldown ---

def test_rejects_when_family_changed_recently() -> None:
    prior = [_prior_run_with_change("observation_coverage", "min_consecutive_runs", 2, 1, _NOW - timedelta(hours=10))]
    can, reason = guards.evaluate(_rec(), 2, prior, [], _NOW, 10)
    assert not can
    assert reason == "cooldown_active"


def test_allows_when_family_change_outside_cooldown() -> None:
    prior = [_prior_run_with_change("observation_coverage", "min_consecutive_runs", 2, 1, _NOW - timedelta(hours=72))]
    can, reason = guards.evaluate(_rec(), 2, prior, [], _NOW, 10)
    assert can
    assert reason == ""


# --- Daily quota ---

def test_rejects_when_daily_quota_exceeded() -> None:
    today = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    prior = [
        _prior_run_with_change("test_visibility", "min_consecutive_runs", 3, 2, today + timedelta(hours=1)),
        _prior_run_with_change("dependency_drift", "min_consecutive_runs", 2, 3, today + timedelta(hours=2)),
    ]
    can, reason = guards.evaluate(_rec(), 2, prior, [], _NOW, 10)
    assert not can
    assert reason == "quota_exceeded"


def test_changes_so_far_count_toward_quota() -> None:
    existing = [TuningChange(family="test_visibility", key="min_consecutive_runs", before=3, after=2, reason="test", applied_at=_NOW)]
    # max_changes_per_day=2; existing=1 from prior runs, 1 in changes_so_far → total=2 → quota exceeded
    prior = [_prior_run_with_change("dependency_drift", "min_consecutive_runs", 2, 3, _NOW.replace(hour=0, minute=30))]
    can, reason = guards.evaluate(_rec(), 2, prior, existing, _NOW, 10)
    assert not can
    assert reason == "quota_exceeded"


# --- Oscillation ---

def test_rejects_oscillation_loosen_after_tighten() -> None:
    # tighten happened recently → attempting loosen should be blocked
    prior = [_prior_run_with_change("observation_coverage", "min_consecutive_runs", 2, 3, _NOW - timedelta(hours=10))]
    can, reason = guards.evaluate(_rec(action="loosen_threshold"), current_value=3, prior_runs=prior, changes_so_far=[], generated_at=_NOW, sample_runs=10)
    assert not can
    assert reason in ("cooldown_active", "oscillation_detected")


# --- Happy path ---

def test_allows_valid_loosen() -> None:
    can, reason = guards.evaluate(_rec(action="loosen_threshold"), current_value=2, prior_runs=[], changes_so_far=[], generated_at=_NOW, sample_runs=10)
    assert can
    assert reason == ""


def test_allows_valid_tighten() -> None:
    can, reason = guards.evaluate(_rec(action="tighten_threshold"), current_value=2, prior_runs=[], changes_so_far=[], generated_at=_NOW, sample_runs=10)
    assert can
    assert reason == ""


# --- compute_new_value ---

def test_compute_new_value_loosen() -> None:
    assert compute_new_value(2, "loosen_threshold") == 1
    assert compute_new_value(1, "loosen_threshold") is None  # below minimum


def test_compute_new_value_tighten() -> None:
    assert compute_new_value(3, "tighten_threshold") == 4
    assert compute_new_value(5, "tighten_threshold") is None  # above maximum


def test_build_skipped_includes_family_and_reason() -> None:
    skipped = guards.build_skipped(_rec(), "quota_exceeded", sample_runs=10)
    assert skipped.family == "observation_coverage"
    assert skipped.reason == "quota_exceeded"
    assert skipped.intended_action == "loosen_threshold"
