"""CoverageGapDeriver — surface test coverage gaps from retained coverage reports.

Emits:
  coverage_gap/low_overall     — total coverage below 60%
  coverage_gap/uncovered_files — files below 80% coverage threshold
"""
from __future__ import annotations

from collections.abc import Sequence

from control_plane.insights.models import DerivedInsight
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import RepoStateSnapshot

_LOW_OVERALL_THRESHOLD = 60.0    # total coverage below this → low_overall insight
_UNCOVERED_FILE_MIN = 3          # need at least this many uncovered files to emit


class CoverageGapDeriver:
    """Derives coverage gap insights from CoverageSignal in observer snapshots."""

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []

        latest = snapshots[0]
        sig = latest.signals.coverage_signal
        if sig.status != "measured" or sig.total_coverage_pct is None:
            return []

        insights: list[DerivedInsight] = []
        first_seen = latest.observed_at
        last_seen = latest.observed_at

        # Look back further for first_seen
        for snap in reversed(snapshots):
            snap_sig = snap.signals.coverage_signal
            if snap_sig.status == "measured":
                first_seen = snap.observed_at
                break

        if sig.total_coverage_pct < _LOW_OVERALL_THRESHOLD:
            insights.append(
                self.normalizer.normalize(
                    kind="coverage_gap/low_overall",
                    subject="coverage_gap",
                    status="present",
                    key_parts=["low_overall"],
                    evidence={
                        "total_coverage_pct": sig.total_coverage_pct,
                        "threshold_pct": _LOW_OVERALL_THRESHOLD,
                        "source": sig.source,
                        "summary": sig.summary,
                    },
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                )
            )

        if sig.uncovered_file_count >= _UNCOVERED_FILE_MIN:
            top_files = [u.path for u in sig.top_uncovered[:5]]
            insights.append(
                self.normalizer.normalize(
                    kind="coverage_gap/uncovered_files",
                    subject="coverage_gap",
                    status="present",
                    key_parts=["uncovered_files"],
                    evidence={
                        "uncovered_file_count": sig.uncovered_file_count,
                        "threshold_pct": sig.uncovered_threshold_pct,
                        "top_uncovered": top_files,
                        "total_coverage_pct": sig.total_coverage_pct,
                        "source": sig.source,
                    },
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                )
            )

        return insights
