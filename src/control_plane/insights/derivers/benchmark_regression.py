from __future__ import annotations

from collections.abc import Sequence

from control_plane.insights.models import DerivedInsight
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import RepoStateSnapshot


class BenchmarkRegressionDeriver:
    """Derive benchmark regression insights from observer snapshots.

    Fires on:
    - benchmark_regression/present: benchmark signal reports regressions.
    """

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []

        bench = snapshots[0].signals.benchmark_signal
        if bench.status == "unavailable":
            return []

        insights: list[DerivedInsight] = []
        observed_at = snapshots[0].observed_at

        if bench.status == "regression" and len(bench.regressions) > 0:
            insights.append(
                self.normalizer.normalize(
                    kind="benchmark_regression",
                    subject="benchmark",
                    status="regression",
                    key_parts=["present"],
                    evidence={
                        "benchmark_count": bench.benchmark_count,
                        "regressions": bench.regressions,
                        "summary": bench.summary or "",
                    },
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                )
            )

        return insights
