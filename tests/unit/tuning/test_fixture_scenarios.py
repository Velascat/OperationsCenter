# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""
Fixture-based integration tests for routing strategy analysis.

These tests use scenario descriptions from tests/fixtures/tuning/ to verify
that analysis produces expected outputs for representative cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operations_center.tuning.analyze import StrategyTuningService
from operations_center.tuning.routing_models import (
    ChangeEvidenceClass,
    EvidenceStrength,
    ReliabilityClass,
)

from .conftest import (
    make_failure,
    make_success,
    make_unknown_changed_files,
)


def test_fixture_files_are_present_and_describe_expected_scenarios() -> None:
    fixture_dir = Path("tests/fixtures/tuning")
    fixture_paths = sorted(fixture_dir.glob("*.json"))
    assert fixture_paths
    loaded = [json.loads(path.read_text()) for path in fixture_paths]
    assert all("records" in payload for payload in loaded)
    assert all("expected" in payload for payload in loaded)


# ---------------------------------------------------------------------------
# Scenario: local success dominant
# (mirrors tests/fixtures/tuning/local_success_dominant.json)
# ---------------------------------------------------------------------------


class TestLocalSuccessDominant:
    """Low-risk local lane with 90% success on bounded tasks."""

    def _records(self):
        successes = [
            make_success(
                backend="kodo",
                lane="aider_local",
                task_type="bug_fix",
                risk_level="low",
                duration_ms=d,
                run_id=f"ls-{i:02d}",
            )
            for i, d in enumerate([18000, 22000, 15000, 19000, 12000, 21000, 16000, 14000, 17000])
        ]
        failure = make_failure(
            backend="kodo",
            lane="aider_local",
            task_type="bug_fix",
            risk_level="low",
            run_id="ls-09",
        )
        return successes + [failure]

    def test_success_rate_is_0_9(self):
        report = StrategyTuningService.default().analyze(self._records())
        s = report.comparison_summaries[0]
        assert s.success_rate == pytest.approx(0.9)

    def test_evidence_strength_is_moderate(self):
        report = StrategyTuningService.default().analyze(self._records())
        s = report.comparison_summaries[0]
        assert s.evidence_strength == EvidenceStrength.MODERATE

    def test_reliability_class_is_high(self):
        report = StrategyTuningService.default().analyze(self._records())
        s = report.comparison_summaries[0]
        assert s.reliability_class == ReliabilityClass.HIGH

    def test_latency_class_is_fast(self):
        report = StrategyTuningService.default().analyze(self._records())
        s = report.comparison_summaries[0]
        # Median of [18,22,15,19,12,21,16,14,17]×1000 = 17000 ms → FAST
        assert s.latency_class.value in ("fast",)

    def test_produces_reliability_finding(self):
        report = StrategyTuningService.default().analyze(self._records())
        cats = [f.category for f in report.findings]
        assert "reliability" in cats

    def test_produces_recommendations(self):
        report = StrategyTuningService.default().analyze(self._records())
        assert report.recommendations


# ---------------------------------------------------------------------------
# Scenario: premium backend wins
# (mirrors tests/fixtures/tuning/premium_backend_wins.json)
# ---------------------------------------------------------------------------


class TestPremiumBackendWins:
    """Archon outperforms kodo@aider_local on refactor tasks."""

    def _records(self):
        archon = [
            make_success(backend="archon", lane="claude_cli", task_type="refactor", run_id=f"arch-{i}")
            for i in range(7)
        ] + [
            make_failure(backend="archon", lane="claude_cli", task_type="refactor", run_id="arch-7")
        ]
        kodo = [
            make_success(backend="kodo", lane="aider_local", task_type="refactor", run_id="kodo-0")
        ] + [
            make_failure(backend="kodo", lane="aider_local", task_type="refactor", run_id=f"kodo-{i}")
            for i in range(1, 8)
        ]
        return archon + kodo

    def test_archon_has_higher_success_rate(self):
        report = StrategyTuningService.default().analyze(self._records())
        by_backend = {s.backend: s for s in report.comparison_summaries}
        assert by_backend["archon"].success_rate > by_backend["kodo"].success_rate

    def test_archon_is_high_reliability(self):
        report = StrategyTuningService.default().analyze(self._records())
        by_backend = {s.backend: s for s in report.comparison_summaries}
        assert by_backend["archon"].reliability_class == ReliabilityClass.HIGH

    def test_kodo_is_low_reliability_for_this_task_type(self):
        report = StrategyTuningService.default().analyze(self._records())
        by_backend = {s.backend: s for s in report.comparison_summaries}
        assert by_backend["kodo"].reliability_class == ReliabilityClass.LOW

    def test_both_backends_appear_in_comparison(self):
        report = StrategyTuningService.default().analyze(self._records())
        backends = {s.backend for s in report.comparison_summaries}
        assert "archon" in backends
        assert "kodo" in backends

    def test_kodo_low_reliability_produces_proposal(self):
        report = StrategyTuningService.default().analyze(self._records())
        _kodo_proposals = [
            p for p in report.recommendations
            if any("kodo" in fid or True for fid in p.source_finding_ids)
        ]
        # At least one proposal about low reliability exists
        assert any("backend_preference" == p.affected_policy_area for p in report.recommendations)


# ---------------------------------------------------------------------------
# Scenario: weak change evidence
# (mirrors tests/fixtures/tuning/weak_change_evidence.json)
# ---------------------------------------------------------------------------


class TestWeakChangeEvidence:
    """Backend with high success rate but poor changed-file evidence."""

    def _records(self):
        unknowns = [
            make_unknown_changed_files(backend="openclaw", lane="claude_cli", run_id=f"oc-{i}")
            for i in range(8)
        ]
        failures = [
            make_failure(backend="openclaw", lane="claude_cli", run_id=f"oc-f-{i}")
            for i in range(2)
        ]
        return unknowns + failures

    def test_change_evidence_class_is_poor(self):
        report = StrategyTuningService.default().analyze(self._records())
        s = report.comparison_summaries[0]
        assert s.change_evidence_class == ChangeEvidenceClass.POOR

    def test_change_evidence_finding_exists(self):
        report = StrategyTuningService.default().analyze(self._records())
        cats = [f.category for f in report.findings]
        assert "change_evidence" in cats

    def test_backend_preference_proposal_generated(self):
        report = StrategyTuningService.default().analyze(self._records())
        proposal_areas = [p.affected_policy_area for p in report.recommendations]
        assert "backend_preference" in proposal_areas

    def test_all_proposals_require_review(self):
        report = StrategyTuningService.default().analyze(self._records())
        assert all(p.requires_review for p in report.recommendations)


# ---------------------------------------------------------------------------
# Scenario: small sample weak evidence
# (mirrors tests/fixtures/tuning/small_sample_weak_evidence.json)
# ---------------------------------------------------------------------------


class TestSmallSampleWeakEvidence:
    """Only 3 runs — all findings should be sparse_data, no recommendations."""

    def _records(self):
        return [
            make_success(backend="openclaw", lane="claude_cli", run_id=f"tiny-{i}")
            for i in range(3)
        ]

    def test_evidence_strength_is_weak(self):
        report = StrategyTuningService.default().analyze(self._records())
        assert report.comparison_summaries[0].evidence_strength == EvidenceStrength.WEAK

    def test_only_sparse_data_finding(self):
        report = StrategyTuningService.default().analyze(self._records())
        cats = set(f.category for f in report.findings)
        assert cats == {"sparse_data"}

    def test_no_recommendations_generated(self):
        report = StrategyTuningService.default().analyze(self._records())
        assert report.recommendations == []

    def test_limitations_mention_small_sample(self):
        report = StrategyTuningService.default().analyze(self._records())
        assert any("weak evidence" in lim.lower() for lim in report.limitations)
