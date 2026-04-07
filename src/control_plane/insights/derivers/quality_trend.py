"""QualityTrendDeriver — objective function for codebase quality measurement.

Computes before/after deltas across retained observer snapshots to answer
"is the codebase actually getting better?"  Uses proxy metrics available in
every snapshot without requiring extra tool runs:

  - lint_signal.violation_count    (ruff errors)
  - type_signal.error_count        (mypy/ty errors)
  - signals.todo_signal.todo_count (TODO/FIXME markers)

Emits:
  - quality_trend/lint_improving     — lint violations falling consistently
  - quality_trend/lint_degrading     — lint violations growing consistently
  - quality_trend/type_improving     — type errors falling consistently
  - quality_trend/type_degrading     — type errors growing consistently
  - quality_trend/stagnant           — no signal in any tracked metric (≥3 snapshots)
"""
from __future__ import annotations

from collections.abc import Sequence

from control_plane.insights.models import DerivedInsight
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import RepoStateSnapshot

_MIN_SNAPSHOTS = 3   # need at least this many to detect a trend


def _int_or_none(val: int | None) -> int | None:
    return val if isinstance(val, int) else None


class QualityTrendDeriver:
    """Derives quality trend insights by comparing metric values across snapshots."""

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if len(snapshots) < _MIN_SNAPSHOTS:
            return []

        # Collect time-ordered metric values (oldest → newest = snapshots reversed)
        ordered = list(reversed(snapshots))

        lint_counts = [
            s.signals.lint_signal.violation_count
            for s in ordered
            if s.signals.lint_signal.status in ("clean", "violations")
        ]
        type_counts = [
            s.signals.type_signal.error_count
            for s in ordered
            if s.signals.type_signal.status in ("clean", "errors")
        ]
        todo_counts = [
            s.signals.todo_signal.todo_count
            for s in ordered
        ]

        first_seen = snapshots[-1].observed_at
        last_seen = snapshots[0].observed_at

        insights: list[DerivedInsight] = []

        def _trend(counts: list[int]) -> str | None:
            """Return 'improving', 'degrading', or None (inconclusive)."""
            if len(counts) < _MIN_SNAPSHOTS:
                return None
            delta = counts[-1] - counts[0]
            # Require at least 10% change relative to starting value to count as a trend
            if counts[0] == 0:
                return "degrading" if delta > 0 else None
            ratio = delta / counts[0]
            if ratio <= -0.10:
                return "improving"
            if ratio >= 0.10:
                return "degrading"
            return None

        lint_trend = _trend(lint_counts)
        if lint_trend:
            insights.append(
                self.normalizer.normalize(
                    kind=f"quality_trend/lint_{lint_trend}",
                    subject="quality_trend",
                    status="present",
                    key_parts=["lint", lint_trend],
                    evidence={
                        "start_count": lint_counts[0] if lint_counts else None,
                        "end_count": lint_counts[-1] if lint_counts else None,
                        "snapshots": len(lint_counts),
                    },
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                )
            )

        type_trend = _trend(type_counts)
        if type_trend:
            insights.append(
                self.normalizer.normalize(
                    kind=f"quality_trend/type_{type_trend}",
                    subject="quality_trend",
                    status="present",
                    key_parts=["type", type_trend],
                    evidence={
                        "start_count": type_counts[0] if type_counts else None,
                        "end_count": type_counts[-1] if type_counts else None,
                        "snapshots": len(type_counts),
                    },
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                )
            )

        # Stagnant: all metrics available but none showing a trend
        if not insights and len(lint_counts) >= _MIN_SNAPSHOTS and len(type_counts) >= _MIN_SNAPSHOTS:
            if lint_trend is None and type_trend is None:
                insights.append(
                    self.normalizer.normalize(
                        kind="quality_trend/stagnant",
                        subject="quality_trend",
                        status="present",
                        key_parts=["stagnant"],
                        evidence={
                            "lint_delta": lint_counts[-1] - lint_counts[0] if lint_counts else 0,
                            "type_delta": type_counts[-1] - type_counts[0] if type_counts else 0,
                            "todo_delta": todo_counts[-1] - todo_counts[0] if todo_counts else 0,
                            "snapshots_analyzed": len(snapshots),
                        },
                        first_seen_at=first_seen,
                        last_seen_at=last_seen,
                    )
                )

        return insights
