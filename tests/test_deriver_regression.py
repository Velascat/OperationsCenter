# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Regression tests for insight derivers and evidence bundle (kodo test)."""


def test_insight_engine_empty_snapshots():
    """F4: InsightEngineService.generate() should not crash on empty snapshot list."""
    from unittest.mock import MagicMock
    from datetime import UTC, datetime
    from operations_center.insights.service import InsightEngineService, InsightGenerationContext

    loader = MagicMock()
    loader.load.return_value = []
    service = InsightEngineService(loader=loader, derivers=[])
    ctx = InsightGenerationContext(
        repo_filter="test", snapshot_run_id=None, history_limit=5,
        run_id="r1", generated_at=datetime.now(UTC), source_command="test",
    )
    artifact, written = service.generate(ctx)
    assert artifact.insights == []


def test_commit_activity_deriver_empty_snapshots():
    """F5: CommitActivityDeriver.derive([]) should return [] not crash."""
    from operations_center.insights.normalizer import InsightNormalizer
    from operations_center.insights.derivers.commit_activity import CommitActivityDeriver
    d = CommitActivityDeriver(InsightNormalizer())
    assert d.derive([]) == []


def test_dependency_drift_deriver_empty_snapshots():
    """F5: DependencyDriftDeriver.derive([]) should return [] not crash."""
    from operations_center.insights.normalizer import InsightNormalizer
    from operations_center.insights.derivers.dependency_drift import DependencyDriftDeriver
    d = DependencyDriftDeriver(InsightNormalizer())
    assert d.derive([]) == []


def test_evidence_bundle_zero_violation_count():
    """F6: violation_count=0 should not fall through to current_count."""
    from operations_center.decision.candidate_builder import _synthesize_evidence_bundle
    bundle = _synthesize_evidence_bundle("lint_fix", {
        "violation_count": 0, "current_count": 42,
    })
    assert bundle is not None
    assert bundle.count == 0, f"Expected 0 but got {bundle.count}"


def test_evidence_bundle_negative_delta_not_worsening():
    """F7: Negative delta should not produce trend='worsening'."""
    from operations_center.decision.candidate_builder import _synthesize_evidence_bundle
    bundle = _synthesize_evidence_bundle("lint_fix", {
        "violation_count": 5, "delta": -3,
    })
    assert bundle is not None
    assert bundle.trend != "worsening", f"Expected improving but got {bundle.trend}"
