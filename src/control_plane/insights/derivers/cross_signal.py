from __future__ import annotations

from collections.abc import Sequence

from control_plane.insights.models import DerivedInsight
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import RepoStateSnapshot

# Require at least this many overlapping files before emitting a correlation insight.
_MIN_OVERLAP_COUNT = 1


class CrossSignalDeriver:
    """Detect when lint/type violations concentrate in the same files as git hotspots.

    Emits:
    - cross_signal/lint_hotspot_overlap: lint-violation files overlap with frequently-changed files.
    - cross_signal/type_hotspot_overlap: type-error files overlap with frequently-changed files.

    These insights are consumed by LintFixRule and TypeImprovementRule to boost confidence when
    the affected files are already under active churn, increasing the signal quality of the proposal.

    File paths are derived from top_violations / top_errors in the observer snapshot; they represent
    a sample of affected files, so overlap counts are lower bounds.
    """

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        current = snapshots[0]
        signals = current.signals

        hotspot_paths = {h.path for h in signals.file_hotspots}
        if not hotspot_paths:
            return []

        insights: list[DerivedInsight] = []

        # Lint ∩ hotspot
        if signals.lint_signal.status == "violations" and signals.lint_signal.top_violations:
            lint_paths = {v.path for v in signals.lint_signal.top_violations}
            overlap = sorted(lint_paths & hotspot_paths)
            if len(overlap) >= _MIN_OVERLAP_COUNT:
                overlap_ratio = round(len(overlap) / len(lint_paths), 2) if lint_paths else 0.0
                insights.append(
                    self.normalizer.normalize(
                        kind="cross_signal",
                        subject="lint_hotspot_overlap",
                        status="present",
                        key_parts=["lint_hotspot_overlap"],
                        evidence={
                            "overlap_files": overlap,
                            "overlap_count": len(overlap),
                            "overlap_ratio": overlap_ratio,
                            "sampled_lint_file_count": len(lint_paths),
                            "hotspot_count": len(hotspot_paths),
                        },
                        first_seen_at=current.observed_at,
                        last_seen_at=current.observed_at,
                    )
                )

        # Type ∩ hotspot
        if signals.type_signal.status == "errors" and signals.type_signal.top_errors:
            type_paths = {e.path for e in signals.type_signal.top_errors}
            overlap = sorted(type_paths & hotspot_paths)
            if len(overlap) >= _MIN_OVERLAP_COUNT:
                overlap_ratio = round(len(overlap) / len(type_paths), 2) if type_paths else 0.0
                insights.append(
                    self.normalizer.normalize(
                        kind="cross_signal",
                        subject="type_hotspot_overlap",
                        status="present",
                        key_parts=["type_hotspot_overlap"],
                        evidence={
                            "overlap_files": overlap,
                            "overlap_count": len(overlap),
                            "overlap_ratio": overlap_ratio,
                            "sampled_type_file_count": len(type_paths),
                            "hotspot_count": len(hotspot_paths),
                        },
                        first_seen_at=current.observed_at,
                        last_seen_at=current.observed_at,
                    )
                )

        return insights
