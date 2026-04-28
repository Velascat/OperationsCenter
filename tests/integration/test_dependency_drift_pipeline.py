"""Integration tests: full dependency_drift pipeline (collector → deriver → rule)."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.rules.dependency_drift import DependencyDriftRule
from operations_center.decision.service import _build_rules, _DEFAULT_ALLOWED_FAMILIES
from operations_center.insights.derivers.dependency_drift import DependencyDriftDeriver
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.collectors.dependency_drift import DependencyDriftCollector
from operations_center.observer.models import (
    ArchitectureSignal,
    BenchmarkSignal,
    CheckSignal,
    DependencyDriftSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    SecuritySignal,
    TodoSignal,
)
from operations_center.observer.service import ObserverContext


def _make_context(tmp_path: Path) -> ObserverContext:
    settings = MagicMock()
    settings.report_root = tmp_path
    return ObserverContext(
        repo_path=tmp_path,
        repo_name="test-repo",
        base_branch="main",
        run_id="pipe_test_001",
        observed_at=datetime.now(UTC),
        source_command="test",
        settings=settings,
        commit_limit=10,
        hotspot_window=30,
        todo_limit=20,
        logs_root=tmp_path / "logs",
    )


def _make_snapshot(
    *,
    dependency_drift_status: str = "not_available",
    observed_at: datetime | None = None,
) -> RepoStateSnapshot:
    now = observed_at or datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status=dependency_drift_status),
        todo_signal=TodoSignal(),
        architecture_signal=ArchitectureSignal(status="unavailable"),
        benchmark_signal=BenchmarkSignal(status="unavailable"),
        security_signal=SecuritySignal(status="unavailable"),
    )
    return RepoStateSnapshot(
        run_id="pipe_test_001",
        observed_at=now,
        source_command="test",
        repo=RepoContextSnapshot(
            name="test-repo",
            path=Path("/tmp/test-repo"),
            current_branch="main",
            base_branch="main",
            is_dirty=False,
        ),
        signals=signals,
    )


class TestFullPipeline:
    """End-to-end: collector → deriver → rule."""

    def test_collector_to_deriver_to_rule(self, tmp_path: Path) -> None:
        """Full pipeline produces CandidateSpec with correct family and fields."""
        # Set up two report dirs so collector returns available
        for i in range(2):
            run_dir = tmp_path / f"run_{i}"
            run_dir.mkdir()
            data = {"statuses": [{"package": "pkg", "notes": "outdated"}]}
            (run_dir / "dependency_report.json").write_text(json.dumps(data))

        ctx = _make_context(tmp_path)
        collector = DependencyDriftCollector()
        signal = collector.collect(ctx)
        assert signal.status == "available"

        # Build two snapshots using the signal status
        t1 = datetime(2026, 4, 8, 12, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        snaps = [
            _make_snapshot(dependency_drift_status=signal.status, observed_at=t1),
            _make_snapshot(dependency_drift_status=signal.status, observed_at=t2),
        ]

        deriver = DependencyDriftDeriver(InsightNormalizer())
        insights = deriver.derive(snaps)
        assert len(insights) == 2  # current + persistent

        rule = DependencyDriftRule(min_consecutive_runs=2)
        candidates = rule.evaluate(insights)
        assert len(candidates) == 1
        spec = candidates[0]
        assert isinstance(spec, CandidateSpec)
        assert spec.family == "dependency_drift_followup"
        assert spec.subject == "dependency_drift"
        assert spec.pattern_key == "present_persistent"
        assert spec.proposal_outline.title_hint is not None

    def test_pipeline_below_threshold_produces_no_candidates(self, tmp_path: Path) -> None:
        """Single snapshot doesn't meet min_consecutive_runs=2."""
        run_dir = tmp_path / "run_0"
        run_dir.mkdir()
        data = {"statuses": [{"package": "pkg", "notes": "outdated"}]}
        (run_dir / "dependency_report.json").write_text(json.dumps(data))

        ctx = _make_context(tmp_path)
        signal = DependencyDriftCollector().collect(ctx)
        assert signal.status == "available"

        snaps = [_make_snapshot(dependency_drift_status=signal.status)]
        insights = DependencyDriftDeriver(InsightNormalizer()).derive(snaps)
        # Only current, no persistent
        assert len(insights) == 1
        assert "current" in insights[0].dedup_key

        candidates = DependencyDriftRule(min_consecutive_runs=2).evaluate(insights)
        assert candidates == []


class TestDecisionEngineServiceConfig:
    """Verify service-level wiring for dependency_drift."""

    def test_build_rules_contains_dependency_drift_rule(self) -> None:
        rules = _build_rules(None)
        drift_rules = [r for r in rules if isinstance(r, DependencyDriftRule)]
        assert len(drift_rules) == 1
        assert drift_rules[0].min_consecutive_runs == 2

    def test_dependency_drift_in_default_allowed_families(self) -> None:
        assert "dependency_drift" in _DEFAULT_ALLOWED_FAMILIES

    def test_gate_vs_output_family_relationship(self) -> None:
        """Gate family is 'dependency_drift' but rule outputs 'dependency_drift_followup'."""
        assert "dependency_drift" in _DEFAULT_ALLOWED_FAMILIES
        # The rule produces candidates with a different family
        rule = DependencyDriftRule(min_consecutive_runs=2)
        from operations_center.insights.models import DerivedInsight
        ts = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        insight = DerivedInsight(
            insight_id="test:persistent",
            dedup_key="dependency_drift_continuity|present|persistent",
            kind="dependency_drift_continuity",
            subject="dependency_drift",
            status="present",
            evidence={"consecutive_snapshots": 3},
            first_seen_at=ts - timedelta(hours=1),
            last_seen_at=ts,
        )
        candidates = rule.evaluate([insight])
        assert len(candidates) == 1
        assert candidates[0].family == "dependency_drift_followup"
        assert candidates[0].family != "dependency_drift"
