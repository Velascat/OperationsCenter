# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for Phase 5 insight derivers: architecture_drift, benchmark_regression, security_vuln."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from operations_center.insights.derivers.architecture_drift import ArchitectureDriftDeriver
from operations_center.insights.derivers.benchmark_regression import BenchmarkRegressionDeriver
from operations_center.insights.derivers.security_vuln import SecurityVulnDeriver
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import (
    ArchitectureSignal,
    BenchmarkSignal,
    DependencyDriftSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    SecuritySignal,
    CheckSignal,
    TodoSignal,
)


def _normalizer() -> InsightNormalizer:
    return InsightNormalizer()


def _make_snapshot(
    *,
    architecture_signal: ArchitectureSignal | None = None,
    benchmark_signal: BenchmarkSignal | None = None,
    security_signal: SecuritySignal | None = None,
) -> RepoStateSnapshot:
    now = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status="not_available"),
        todo_signal=TodoSignal(),
        architecture_signal=architecture_signal or ArchitectureSignal(status="unavailable"),
        benchmark_signal=benchmark_signal or BenchmarkSignal(status="unavailable"),
        security_signal=security_signal or SecuritySignal(status="unavailable"),
    )
    return RepoStateSnapshot(
        run_id="obs_test_001",
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


# ── ArchitectureDriftDeriver ─────────────────────────────────────────


class TestArchitectureDriftDeriver:
    def test_empty_snapshots(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        assert deriver.derive([]) == []

    def test_unavailable_signal(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(architecture_signal=ArchitectureSignal(status="unavailable"))
        assert deriver.derive([snap]) == []

    def test_healthy_no_insights(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="healthy",
                coupling_score=0.3,
                max_import_depth=2,
                circular_dependencies=[],
                summary="all good",
            )
        )
        assert deriver.derive([snap]) == []

    def test_coupling_high(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings",
                coupling_score=0.85,
                max_import_depth=3,
                circular_dependencies=["a -> b -> a"],
                summary="high coupling",
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert insights[0].kind == "arch_drift"
        assert insights[0].subject == "coupling"
        assert insights[0].status == "high"
        assert insights[0].evidence["coupling_score"] == 0.85
        assert insights[0].evidence["circular_dependencies"] == ["a -> b -> a"]
        assert "arch_drift" in insights[0].dedup_key
        assert "coupling_high" in insights[0].dedup_key

    def test_module_bloat(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings",
                coupling_score=0.2,
                max_import_depth=8,
                summary="deep imports",
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert insights[0].kind == "arch_drift"
        assert insights[0].subject == "module_depth"
        assert insights[0].status == "bloated"
        assert insights[0].evidence["max_import_depth"] == 8
        assert "module_bloat" in insights[0].dedup_key

    def test_both_coupling_and_bloat(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings",
                coupling_score=1.2,
                max_import_depth=10,
                circular_dependencies=[],
                summary="both issues",
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 2
        kinds = {i.dedup_key for i in insights}
        assert any("coupling_high" in k for k in kinds)
        assert any("module_bloat" in k for k in kinds)

    def test_coupling_at_threshold(self) -> None:
        """Exactly 0.7 should trigger."""
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings", coupling_score=0.7, max_import_depth=2, summary=""
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert "coupling_high" in insights[0].dedup_key

    def test_depth_at_threshold(self) -> None:
        """Exactly 6 should trigger."""
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings", coupling_score=0.1, max_import_depth=6, summary=""
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert "module_bloat" in insights[0].dedup_key

    def test_below_thresholds(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="healthy", coupling_score=0.69, max_import_depth=5, summary=""
            )
        )
        assert deriver.derive([snap]) == []

    def test_timestamps(self) -> None:
        deriver = ArchitectureDriftDeriver(_normalizer())
        snap = _make_snapshot(
            architecture_signal=ArchitectureSignal(
                status="warnings", coupling_score=0.9, max_import_depth=2, summary=""
            )
        )
        insights = deriver.derive([snap])
        assert insights[0].first_seen_at == snap.observed_at
        assert insights[0].last_seen_at == snap.observed_at


# ── BenchmarkRegressionDeriver ───────────────────────────────────────


class TestBenchmarkRegressionDeriver:
    def test_empty_snapshots(self) -> None:
        deriver = BenchmarkRegressionDeriver(_normalizer())
        assert deriver.derive([]) == []

    def test_unavailable_signal(self) -> None:
        deriver = BenchmarkRegressionDeriver(_normalizer())
        snap = _make_snapshot(benchmark_signal=BenchmarkSignal(status="unavailable"))
        assert deriver.derive([snap]) == []

    def test_nominal_no_insights(self) -> None:
        deriver = BenchmarkRegressionDeriver(_normalizer())
        snap = _make_snapshot(
            benchmark_signal=BenchmarkSignal(
                status="nominal", benchmark_count=5, regressions=[], summary="ok"
            )
        )
        assert deriver.derive([snap]) == []

    def test_regression_present(self) -> None:
        deriver = BenchmarkRegressionDeriver(_normalizer())
        snap = _make_snapshot(
            benchmark_signal=BenchmarkSignal(
                status="regression",
                source="pytest_benchmark",
                benchmark_count=3,
                regressions=["test_slow: stddev (0.05) > 2x mean (0.01)"],
                summary="3 benchmark(s) found; 1 regression(s)",
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        i = insights[0]
        assert i.kind == "benchmark_regression"
        assert i.subject == "benchmark"
        assert i.status == "regression"
        assert i.evidence["benchmark_count"] == 3
        assert len(i.evidence["regressions"]) == 1
        assert "benchmark_regression" in i.dedup_key
        assert "present" in i.dedup_key
        assert i.first_seen_at == snap.observed_at
        assert i.last_seen_at == snap.observed_at

    def test_regression_status_but_empty_list(self) -> None:
        """status=regression but empty regressions list should not emit."""
        deriver = BenchmarkRegressionDeriver(_normalizer())
        snap = _make_snapshot(
            benchmark_signal=BenchmarkSignal(
                status="regression", benchmark_count=1, regressions=[]
            )
        )
        assert deriver.derive([snap]) == []


# ── SecurityVulnDeriver ──────────────────────────────────────────────


class TestSecurityVulnDeriver:
    def test_empty_snapshots(self) -> None:
        deriver = SecurityVulnDeriver(_normalizer())
        assert deriver.derive([]) == []

    def test_unavailable_signal(self) -> None:
        deriver = SecurityVulnDeriver(_normalizer())
        snap = _make_snapshot(security_signal=SecuritySignal(status="unavailable"))
        assert deriver.derive([snap]) == []

    def test_clean_no_insights(self) -> None:
        deriver = SecurityVulnDeriver(_normalizer())
        snap = _make_snapshot(
            security_signal=SecuritySignal(
                status="clean", advisory_count=0, critical_count=0, high_count=0
            )
        )
        assert deriver.derive([snap]) == []

    def test_advisories_present(self) -> None:
        deriver = SecurityVulnDeriver(_normalizer())
        snap = _make_snapshot(
            security_signal=SecuritySignal(
                status="advisories",
                source="npm_audit",
                advisory_count=5,
                critical_count=1,
                high_count=2,
                summary="5 advisory(ies); 1 critical; 2 high",
            )
        )
        insights = deriver.derive([snap])
        assert len(insights) == 1
        i = insights[0]
        assert i.kind == "security_vuln"
        assert i.subject == "security"
        assert i.status == "advisories"
        assert i.evidence["advisory_count"] == 5
        assert i.evidence["critical_count"] == 1
        assert i.evidence["high_count"] == 2
        assert "security_vuln" in i.dedup_key
        assert "present" in i.dedup_key
        assert i.first_seen_at == snap.observed_at
        assert i.last_seen_at == snap.observed_at

    def test_advisories_status_but_zero_count(self) -> None:
        """status=advisories but advisory_count=0 should not emit."""
        deriver = SecurityVulnDeriver(_normalizer())
        snap = _make_snapshot(
            security_signal=SecuritySignal(status="advisories", advisory_count=0)
        )
        assert deriver.derive([snap]) == []


# ── Wiring test ──────────────────────────────────────────────────────


class TestBuildInsightServiceWiring:
    def test_derivers_include_phase5_before_cross_signal(self) -> None:
        """All three Phase 5 derivers appear in the derivers list before CrossSignalDeriver."""
        from operations_center.entrypoints.autonomy_cycle.main import build_insight_service

        service = build_insight_service()
        deriver_types = [type(d).__name__ for d in service.derivers]

        assert "ArchitectureDriftDeriver" in deriver_types
        assert "BenchmarkRegressionDeriver" in deriver_types
        assert "SecurityVulnDeriver" in deriver_types
        assert "CrossSignalDeriver" in deriver_types

        cross_idx = deriver_types.index("CrossSignalDeriver")
        assert deriver_types.index("ArchitectureDriftDeriver") < cross_idx
        assert deriver_types.index("BenchmarkRegressionDeriver") < cross_idx
        assert deriver_types.index("SecurityVulnDeriver") < cross_idx
