# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for the arch_promotion pipeline: ArchSchedulerDeriver + ArchPromotionRule."""
from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import MagicMock

from operations_center.insights.derivers.arch_scheduler import (
    ArchSchedulerDeriver,
    _MIN_RUNS,
)
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.decision.rules.arch_promotion import ArchPromotionRule
from operations_center.observer.models import (
    BacklogItem,
    BacklogSignal,
    DependencyDriftSignal,
    ExecutionHealthSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    CheckSignal,
    TodoSignal,
)
from operations_center.tuning.models import TuningRecommendation, TuningRunArtifact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(
    *,
    arch_items: list[BacklogItem] | None = None,
    total_runs: int = 15,
    no_op_count: int = 2,
    validation_failed_count: int = 0,
    repo_name: str = "myrepo",
) -> RepoStateSnapshot:
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status="not_available"),
        todo_signal=TodoSignal(),
        execution_health=ExecutionHealthSignal(
            total_runs=total_runs,
            no_op_count=no_op_count,
            validation_failed_count=validation_failed_count,
        ),
        backlog=BacklogSignal(items=arch_items or []),
    )
    return RepoStateSnapshot(
        run_id="obs_test",
        observed_at=datetime.now(UTC),
        source_command="test",
        repo=RepoContextSnapshot(
            name=repo_name,
            path=Path("/repo"),
            current_branch="main",
            is_dirty=False,
        ),
        signals=signals,
    )


def _stable_tuning_artifact() -> TuningRunArtifact:
    return TuningRunArtifact(
        run_id="tuning_test",
        generated_at=datetime.now(UTC),
        source_command="test",
        window_runs=20,
        recommendations=[
            TuningRecommendation(family="observation_coverage", action="keep", rationale="healthy", confidence="high"),
            TuningRecommendation(family="test_visibility", action="keep", rationale="healthy", confidence="high"),
            TuningRecommendation(family="dependency_drift", action="keep", rationale="healthy", confidence="high"),
        ],
    )


def _unstable_tuning_artifact(bad_family: str, action: str = "loosen_threshold") -> TuningRunArtifact:
    recs = [
        TuningRecommendation(family="observation_coverage", action="keep", rationale="ok", confidence="high"),
        TuningRecommendation(family="test_visibility", action="keep", rationale="ok", confidence="high"),
        TuningRecommendation(family="dependency_drift", action="keep", rationale="ok", confidence="high"),
    ]
    # Replace the bad one
    recs = [r if r.family != bad_family else TuningRecommendation(family=bad_family, action=action, rationale="unstable", confidence="low") for r in recs]
    return TuningRunArtifact(
        run_id="tuning_test",
        generated_at=datetime.now(UTC),
        source_command="test",
        window_runs=20,
        recommendations=recs,
    )


def _make_deriver(tuning_artifact: TuningRunArtifact | None = None) -> ArchSchedulerDeriver:
    loader = MagicMock()
    loader.load_recent.return_value = [tuning_artifact] if tuning_artifact else []
    return ArchSchedulerDeriver(InsightNormalizer(), tuning_loader=loader)


_ARCH_ITEM = BacklogItem(title="Rearchitect the pipeline", item_type="arch", description="Big refactor.")
_REDESIGN_ITEM = BacklogItem(title="Redesign data model", item_type="redesign", description="New schema.")


# ---------------------------------------------------------------------------
# Gate: no arch items → no output
# ---------------------------------------------------------------------------

def test_no_arch_items_returns_empty():
    items = [BacklogItem(title="Add CI check", item_type="maintenance")]
    deriver = _make_deriver(_stable_tuning_artifact())
    assert deriver.derive([_make_snapshot(arch_items=items)]) == []


def test_empty_snapshots_returns_empty():
    assert _make_deriver(_stable_tuning_artifact()).derive([]) == []


# ---------------------------------------------------------------------------
# Gate: all pass → arch_backlog_item insights emitted
# ---------------------------------------------------------------------------

def test_all_gates_pass_emits_arch_insights():
    deriver = _make_deriver(_stable_tuning_artifact())
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM, _REDESIGN_ITEM])])
    kinds = [i.kind for i in insights]
    assert kinds == ["arch_backlog_item", "arch_backlog_item"]


def test_emitted_insights_carry_title_and_type():
    deriver = _make_deriver(_stable_tuning_artifact())
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM])])
    assert insights[0].evidence["title"] == "Rearchitect the pipeline"
    assert insights[0].evidence["item_type"] == "arch"


# ---------------------------------------------------------------------------
# Gate: insufficient runs
# ---------------------------------------------------------------------------

def test_blocked_when_insufficient_runs():
    deriver = _make_deriver(_stable_tuning_artifact())
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM], total_runs=_MIN_RUNS - 1)])
    assert len(insights) == 1
    assert insights[0].kind == "arch_schedule_blocked"
    assert any("insufficient execution history" in r for r in insights[0].evidence["reasons"])


# ---------------------------------------------------------------------------
# Gate: no-op rate too high
# ---------------------------------------------------------------------------

def test_blocked_when_no_op_rate_too_high():
    # 10/15 = 67% > 30%
    deriver = _make_deriver(_stable_tuning_artifact())
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM], total_runs=15, no_op_count=10)])
    assert insights[0].kind == "arch_schedule_blocked"
    assert any("no-op rate" in r for r in insights[0].evidence["reasons"])


def test_passes_when_no_op_rate_just_below_threshold():
    # 4/15 = 27% < 30%
    deriver = _make_deriver(_stable_tuning_artifact())
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM], total_runs=15, no_op_count=4)])
    assert all(i.kind == "arch_backlog_item" for i in insights)


# ---------------------------------------------------------------------------
# Gate: validation failures
# ---------------------------------------------------------------------------

def test_blocked_when_validation_failures_present():
    deriver = _make_deriver(_stable_tuning_artifact())
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM], validation_failed_count=1)])
    assert insights[0].kind == "arch_schedule_blocked"
    assert any("validation failures" in r for r in insights[0].evidence["reasons"])


def test_passes_when_zero_validation_failures():
    deriver = _make_deriver(_stable_tuning_artifact())
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM], validation_failed_count=0)])
    assert all(i.kind == "arch_backlog_item" for i in insights)


# ---------------------------------------------------------------------------
# Gate: tuning stability
# ---------------------------------------------------------------------------

def test_blocked_when_no_tuning_artifact():
    deriver = _make_deriver(tuning_artifact=None)
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM])])
    assert insights[0].kind == "arch_schedule_blocked"
    assert any("no tune-autonomy run" in r for r in insights[0].evidence["reasons"])


def test_blocked_when_family_not_keep():
    deriver = _make_deriver(_unstable_tuning_artifact("test_visibility", "loosen_threshold"))
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM])])
    assert insights[0].kind == "arch_schedule_blocked"
    assert any("tuning not stable" in r for r in insights[0].evidence["reasons"])


def test_blocked_when_family_missing_from_recommendations():
    artifact = TuningRunArtifact(
        run_id="t", generated_at=datetime.now(UTC), source_command="test", window_runs=5,
        recommendations=[
            TuningRecommendation(family="observation_coverage", action="keep", rationale="ok", confidence="high"),
            # test_visibility and dependency_drift missing
        ],
    )
    loader = MagicMock()
    loader.load_recent.return_value = [artifact]
    deriver = ArchSchedulerDeriver(InsightNormalizer(), tuning_loader=loader)
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM])])
    assert insights[0].kind == "arch_schedule_blocked"


# ---------------------------------------------------------------------------
# Gate: multiple failures accumulate
# ---------------------------------------------------------------------------

def test_all_failures_accumulate_in_reasons():
    deriver = _make_deriver(_unstable_tuning_artifact("observation_coverage", "tighten_threshold"))
    insights = deriver.derive([_make_snapshot(
        arch_items=[_ARCH_ITEM],
        total_runs=5,          # too few
        no_op_count=3,         # 60% — too high
        validation_failed_count=2,  # nonzero
    )])
    assert insights[0].kind == "arch_schedule_blocked"
    reasons = insights[0].evidence["reasons"]
    assert len(reasons) >= 3  # at least: runs, no-op, validation


# ---------------------------------------------------------------------------
# blocked insight carries pending items
# ---------------------------------------------------------------------------

def test_blocked_insight_carries_pending_item_titles():
    deriver = _make_deriver(tuning_artifact=None)
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM, _REDESIGN_ITEM])])
    pending = insights[0].evidence["pending_items"]
    assert "Rearchitect the pipeline" in pending
    assert "Redesign data model" in pending


# ---------------------------------------------------------------------------
# Decision rule
# ---------------------------------------------------------------------------

def test_rule_emits_arch_promotion_candidate():
    deriver = _make_deriver(_stable_tuning_artifact())
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM])])
    candidates = ArchPromotionRule().evaluate(insights)
    assert len(candidates) == 1
    assert candidates[0].family == "arch_promotion"
    assert candidates[0].proposal_outline.title_hint == "Rearchitect the pipeline"


def test_rule_ignores_blocked_insights():
    deriver = _make_deriver(tuning_artifact=None)
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM])])
    assert insights[0].kind == "arch_schedule_blocked"
    candidates = ArchPromotionRule().evaluate(insights)
    assert candidates == []


def test_rule_ignores_non_arch_insights():
    from operations_center.insights.models import DerivedInsight
    other = DerivedInsight(
        insight_id="x",
        kind="backlog_item",
        subject="something",
        status="pending",
        dedup_key="backlog|repo|something",
        evidence={},
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    assert ArchPromotionRule().evaluate([other]) == []


def test_arch_promotion_not_in_default_families():
    from operations_center.decision.service import _DEFAULT_ALLOWED_FAMILIES, ALL_FAMILIES
    assert "arch_promotion" not in _DEFAULT_ALLOWED_FAMILIES
    assert "arch_promotion" in ALL_FAMILIES


def test_summary_mentions_health_gate():
    deriver = _make_deriver(_stable_tuning_artifact())
    insights = deriver.derive([_make_snapshot(arch_items=[_ARCH_ITEM])])
    candidates = ArchPromotionRule().evaluate(insights)
    assert "stable" in candidates[0].proposal_outline.summary_hint.lower()
