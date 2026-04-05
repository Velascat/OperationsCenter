"""Tests for the cross-signal correlation deriver and its effect on decision rules.

Covers:
- Overlapping lint + hotspot paths emit a cross_signal insight.
- Overlapping type + hotspot paths emit a cross_signal insight.
- Non-overlapping paths do not emit a cross_signal insight.
- cross_signal evidence includes overlap_files, overlap_count, overlap_ratio.
- LintFixRule boosts confidence to 'high' when cross_signal insight is present.
- TypeImprovementRule boosts confidence to 'high' when cross_signal insight is present.
- Isolated signals (no cross_signal insight) keep their original confidence.
"""
from __future__ import annotations

from datetime import UTC, datetime

from control_plane.decision.rules.lint_fix import LintFixRule
from control_plane.decision.rules.type_improvement import TypeImprovementRule
from control_plane.insights.derivers.cross_signal import CrossSignalDeriver
from control_plane.insights.models import DerivedInsight
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import (
    DependencyDriftSignal,
    FileHotspot,
    LintSignal,
    LintViolation,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TestSignal,
    TodoSignal,
    TypeSignal,
    TypeError,
)

_NOW = datetime(2026, 4, 5, 12, tzinfo=UTC)


def _snapshot(
    *,
    lint_paths: list[str] | None = None,
    type_paths: list[str] | None = None,
    hotspot_paths: list[str] | None = None,
) -> RepoStateSnapshot:
    violations = [LintViolation(path=p, line=1, col=1, code="E501", message="x") for p in (lint_paths or [])]
    type_errors = [TypeError(path=p, line=1, col=1, code="attr", message="y") for p in (type_paths or [])]
    hotspots = [FileHotspot(path=p, touch_count=5) for p in (hotspot_paths or [])]
    signals = RepoSignalsSnapshot(
        test_signal=TestSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status="unknown"),
        todo_signal=TodoSignal(),
        lint_signal=LintSignal(
            status="violations" if violations else "clean",
            violation_count=len(violations),
            top_violations=violations,
        ),
        type_signal=TypeSignal(
            status="errors" if type_errors else "clean",
            error_count=len(type_errors),
            top_errors=type_errors,
        ),
        file_hotspots=hotspots,
    )
    return RepoStateSnapshot(
        run_id="obs_test",
        observed_at=_NOW,
        source_command="test",
        repo=RepoContextSnapshot(
            name="repo",
            path=__import__("pathlib").Path("/tmp/repo"),
            current_branch="main",
            is_dirty=False,
        ),
        signals=signals,
    )


def _deriver() -> CrossSignalDeriver:
    return CrossSignalDeriver(InsightNormalizer())


# ── emission ────────────────────────────────────────────────────────────────


def test_lint_hotspot_overlap_emits_insight() -> None:
    snap = _snapshot(
        lint_paths=["src/a.py", "src/b.py"],
        hotspot_paths=["src/a.py", "src/c.py"],
    )
    insights = _deriver().derive([snap])
    overlap = [i for i in insights if i.subject == "lint_hotspot_overlap"]
    assert len(overlap) == 1
    ev = overlap[0].evidence
    assert ev["overlap_count"] == 1
    assert "src/a.py" in ev["overlap_files"]
    assert 0 < ev["overlap_ratio"] <= 1.0


def test_type_hotspot_overlap_emits_insight() -> None:
    snap = _snapshot(
        type_paths=["src/x.py", "src/y.py"],
        hotspot_paths=["src/y.py", "src/z.py"],
    )
    insights = _deriver().derive([snap])
    overlap = [i for i in insights if i.subject == "type_hotspot_overlap"]
    assert len(overlap) == 1
    assert overlap[0].evidence["overlap_count"] == 1


def test_no_overlap_emits_no_cross_signal() -> None:
    snap = _snapshot(
        lint_paths=["src/a.py"],
        hotspot_paths=["src/b.py"],
    )
    insights = _deriver().derive([snap])
    assert all(i.kind != "cross_signal" for i in insights)


def test_no_hotspots_emits_nothing() -> None:
    snap = _snapshot(lint_paths=["src/a.py"], hotspot_paths=[])
    insights = _deriver().derive([snap])
    assert all(i.kind != "cross_signal" for i in insights)


def test_both_overlap_emit_two_insights() -> None:
    snap = _snapshot(
        lint_paths=["src/a.py"],
        type_paths=["src/a.py"],
        hotspot_paths=["src/a.py"],
    )
    insights = _deriver().derive([snap])
    cross = [i for i in insights if i.kind == "cross_signal"]
    subjects = {i.subject for i in cross}
    assert "lint_hotspot_overlap" in subjects
    assert "type_hotspot_overlap" in subjects


# ── confidence boosting in rules ─────────────────────────────────────────


def _lint_present_insight(*, count: int = 8, distinct_file_count: int = 2) -> DerivedInsight:
    return DerivedInsight(
        insight_id="lint:lint_violations:present",
        dedup_key="lint:lint_violations:present",
        kind="lint_drift",
        subject="lint_violations",
        status="present",
        evidence={
            "violation_count": count,
            "distinct_file_count": distinct_file_count,
            "top_codes": ["E501"],
            "source": "ruff",
        },
        first_seen_at=_NOW,
        last_seen_at=_NOW,
    )


def _type_present_insight(*, count: int = 5, distinct_file_count: int = 2) -> DerivedInsight:
    return DerivedInsight(
        insight_id="type:type_errors:present",
        dedup_key="type:type_errors:present",
        kind="type_health",
        subject="type_errors",
        status="present",
        evidence={
            "error_count": count,
            "distinct_file_count": distinct_file_count,
            "top_codes": ["attr-defined"],
            "source": "mypy",
        },
        first_seen_at=_NOW,
        last_seen_at=_NOW,
    )


def _cross_lint_insight() -> DerivedInsight:
    return DerivedInsight(
        insight_id="cross:lint_hotspot_overlap",
        dedup_key="cross:lint_hotspot_overlap",
        kind="cross_signal",
        subject="lint_hotspot_overlap",
        status="present",
        evidence={"overlap_files": ["src/a.py"], "overlap_count": 1, "overlap_ratio": 0.5},
        first_seen_at=_NOW,
        last_seen_at=_NOW,
    )


def _cross_type_insight() -> DerivedInsight:
    return DerivedInsight(
        insight_id="cross:type_hotspot_overlap",
        dedup_key="cross:type_hotspot_overlap",
        kind="cross_signal",
        subject="type_hotspot_overlap",
        status="present",
        evidence={"overlap_files": ["src/x.py"], "overlap_count": 1, "overlap_ratio": 0.5},
        first_seen_at=_NOW,
        last_seen_at=_NOW,
    )


def test_lint_rule_boosts_confidence_when_hotspot_overlap_present() -> None:
    rule = LintFixRule(min_violations=1)
    # Without cross_signal: count < 20, so confidence should be "medium"
    specs_no_cross = rule.evaluate([_lint_present_insight(count=8)])
    assert specs_no_cross[0].confidence == "medium"

    # With cross_signal: count < 20 but overlap present → confidence "high"
    specs_with_cross = rule.evaluate([_lint_present_insight(count=8), _cross_lint_insight()])
    assert specs_with_cross[0].confidence == "high"
    assert "cross_signal_lint_hotspot_overlap" in specs_with_cross[0].matched_rules


def test_type_rule_boosts_confidence_when_hotspot_overlap_present() -> None:
    rule = TypeImprovementRule(min_errors=1)
    specs_no_cross = rule.evaluate([_type_present_insight(count=5)])
    assert specs_no_cross[0].confidence == "medium"

    specs_with_cross = rule.evaluate([_type_present_insight(count=5), _cross_type_insight()])
    assert specs_with_cross[0].confidence == "high"
    assert "cross_signal_type_hotspot_overlap" in specs_with_cross[0].matched_rules


def test_lint_rule_high_count_already_high_unaffected_by_cross() -> None:
    """Count >= 20 already means high confidence; cross_signal doesn't change that."""
    rule = LintFixRule(min_violations=1)
    specs = rule.evaluate([_lint_present_insight(count=25), _cross_lint_insight()])
    assert specs[0].confidence == "high"


def test_cross_signal_for_wrong_type_does_not_boost() -> None:
    """A type_hotspot_overlap insight does not boost lint_fix confidence."""
    rule = LintFixRule(min_violations=1)
    # type overlap present, but no lint overlap
    specs = rule.evaluate([_lint_present_insight(count=8), _cross_type_insight()])
    assert specs[0].confidence == "medium"


def test_estimated_affected_files_passed_to_spec() -> None:
    rule = LintFixRule(min_violations=1)
    specs = rule.evaluate([_lint_present_insight(count=8, distinct_file_count=4)])
    assert specs[0].estimated_affected_files == 4
