# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the backlog_promotion pipeline: collector → deriver → rule."""
from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path


from operations_center.observer.collectors.backlog import (
    BacklogCollector,
    _parse_backlog,
    promotable_items,
)
from operations_center.observer.models import BacklogItem, BacklogSignal, RepoSignalsSnapshot, RepoStateSnapshot, RepoContextSnapshot, CheckSignal, DependencyDriftSignal, TodoSignal
from operations_center.insights.derivers.backlog_promotion import BacklogPromotionDeriver
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.decision.rules.backlog_promotion import BacklogPromotionRule


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_SAMPLE_BACKLOG = """
# Backlog

## Active

### some — Active item
**Status**: in progress

---

## Next

### autonomy — Promote hotspot families
**Type**: maintenance
After observing healthy rates, promote hotspot families.

### config — Per-repo budget overrides
**Type**: feature
Allow repos to declare their own caps.

### arch — Redesign the whole pipeline
**Type**: arch
Big rearchitecture of everything.

### redesign — Rethink the data model
**Type**: redesign
Completely rethink the data model.

## Completed

### old — Done thing
**Status**: done
"""


def test_parse_only_next_section():
    signal = _parse_backlog(_SAMPLE_BACKLOG)
    titles = [item.title for item in signal.items]
    assert "autonomy — Promote hotspot families" in titles
    assert "config — Per-repo budget overrides" in titles
    # Active and Completed sections must not bleed in
    assert not any("Active item" in t for t in titles)
    assert not any("Done thing" in t for t in titles)


def test_parse_types():
    signal = _parse_backlog(_SAMPLE_BACKLOG)
    by_title = {item.title: item for item in signal.items}
    assert by_title["autonomy — Promote hotspot families"].item_type == "maintenance"
    assert by_title["config — Per-repo budget overrides"].item_type == "feature"
    assert by_title["arch — Redesign the whole pipeline"].item_type == "arch"
    assert by_title["redesign — Rethink the data model"].item_type == "redesign"


def test_parse_description():
    signal = _parse_backlog(_SAMPLE_BACKLOG)
    by_title = {item.title: item for item in signal.items}
    assert "promote hotspot families" in by_title["autonomy — Promote hotspot families"].description.lower()


def test_promotable_items_excludes_arch_and_redesign():
    signal = _parse_backlog(_SAMPLE_BACKLOG)
    promotable = promotable_items(signal)
    types = {item.item_type for item in promotable}
    assert "arch" not in types
    assert "redesign" not in types


def test_promotable_items_includes_maintenance_and_feature():
    signal = _parse_backlog(_SAMPLE_BACKLOG)
    promotable = promotable_items(signal)
    titles = [item.title for item in promotable]
    assert "autonomy — Promote hotspot families" in titles
    assert "config — Per-repo budget overrides" in titles


def test_empty_backlog_returns_empty_signal():
    signal = _parse_backlog("")
    assert signal.items == []


def test_no_next_section_returns_empty_signal():
    signal = _parse_backlog("## Active\n### foo\nsome item\n")
    assert signal.items == []


def test_default_type_is_feature_when_untagged():
    text = "## Next\n\n### untagged — Some item\nNo type line here.\n"
    signal = _parse_backlog(text)
    assert signal.items[0].item_type == "feature"


# ---------------------------------------------------------------------------
# Collector (filesystem)
# ---------------------------------------------------------------------------

def test_collector_reads_backlog_file(tmp_path: Path):
    backlog = tmp_path / "docs" / "backlog.md"
    backlog.parent.mkdir()
    backlog.write_text("## Next\n\n### ci — Add CI check\n**Type**: maintenance\nRun checks in CI.\n")
    signal = BacklogCollector().collect(_fake_context(tmp_path))
    assert len(signal.items) == 1
    assert signal.items[0].item_type == "maintenance"


def test_collector_returns_empty_when_no_backlog(tmp_path: Path):
    signal = BacklogCollector().collect(_fake_context(tmp_path))
    assert signal.items == []


# ---------------------------------------------------------------------------
# Deriver
# ---------------------------------------------------------------------------

def _make_snapshot(items: list[BacklogItem], repo_name: str = "myrepo") -> RepoStateSnapshot:
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status="not_available"),
        todo_signal=TodoSignal(),
        backlog=BacklogSignal(items=items),
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


def test_deriver_emits_one_insight_per_promotable_item():
    items = [
        BacklogItem(title="Feature A", item_type="feature"),
        BacklogItem(title="Arch overhaul", item_type="arch"),
        BacklogItem(title="Maintenance B", item_type="maintenance"),
    ]
    deriver = BacklogPromotionDeriver(InsightNormalizer())
    insights = deriver.derive([_make_snapshot(items)])
    titles = [i.subject for i in insights]
    assert "Feature A" in titles
    assert "Maintenance B" in titles
    assert "Arch overhaul" not in titles


def test_deriver_returns_empty_for_no_snapshots():
    deriver = BacklogPromotionDeriver(InsightNormalizer())
    assert deriver.derive([]) == []


def test_deriver_insight_kind_is_backlog_item():
    items = [BacklogItem(title="Do thing", item_type="feature")]
    deriver = BacklogPromotionDeriver(InsightNormalizer())
    insights = deriver.derive([_make_snapshot(items)])
    assert all(i.kind == "backlog_item" for i in insights)


# ---------------------------------------------------------------------------
# Decision rule
# ---------------------------------------------------------------------------

def test_rule_emits_candidate_per_backlog_insight():
    items = [
        BacklogItem(title="Add CI artifact", item_type="maintenance"),
        BacklogItem(title="Budget overrides", item_type="feature"),
    ]
    deriver = BacklogPromotionDeriver(InsightNormalizer())
    insights = deriver.derive([_make_snapshot(items)])
    rule = BacklogPromotionRule()
    candidates = rule.evaluate(insights)
    assert len(candidates) == 2
    assert all(c.family == "backlog_promotion" for c in candidates)


def test_rule_skips_non_backlog_insights():
    from operations_center.insights.models import DerivedInsight
    other = DerivedInsight(
        insight_id="test-id",
        kind="observation_coverage",
        subject="repo",
        status="present",
        dedup_key="obs|repo",
        evidence={},
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    candidates = BacklogPromotionRule().evaluate([other])
    assert candidates == []


def test_rule_title_hint_matches_backlog_title():
    items = [BacklogItem(title="Enforce analyze-artifacts in CI", item_type="maintenance")]
    deriver = BacklogPromotionDeriver(InsightNormalizer())
    insights = deriver.derive([_make_snapshot(items)])
    candidates = BacklogPromotionRule().evaluate(insights)
    assert candidates[0].proposal_outline.title_hint == "Enforce analyze-artifacts in CI"


def test_backlog_promotion_not_in_default_families():
    from operations_center.decision.service import _DEFAULT_ALLOWED_FAMILIES, ALL_FAMILIES
    assert "backlog_promotion" not in _DEFAULT_ALLOWED_FAMILIES
    assert "backlog_promotion" in ALL_FAMILIES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeContext:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path


def _fake_context(repo_path: Path) -> _FakeContext:
    return _fake_context_obj(repo_path)


class _fake_context_obj:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
