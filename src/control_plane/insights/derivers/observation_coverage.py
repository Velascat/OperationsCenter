from __future__ import annotations

from collections.abc import Sequence

from control_plane.insights.models import DerivedInsight
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import RepoStateSnapshot


class ObservationCoverageDeriver:
    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        current = snapshots[0]
        insights: list[DerivedInsight] = []
        unavailable_signals = set(current.collector_errors.keys())
        if current.signals.test_signal.status == "unknown":
            unavailable_signals.add("test_signal")
        if current.signals.dependency_drift.status == "not_available":
            unavailable_signals.add("dependency_drift")

        for signal in sorted(unavailable_signals):
            consecutive = 0
            matching: list[RepoStateSnapshot] = []
            for snapshot in snapshots:
                snapshot_unavailable = set(snapshot.collector_errors.keys())
                if snapshot.signals.test_signal.status == "unknown":
                    snapshot_unavailable.add("test_signal")
                if snapshot.signals.dependency_drift.status == "not_available":
                    snapshot_unavailable.add("dependency_drift")
                if signal not in snapshot_unavailable:
                    break
                consecutive += 1
                matching.append(snapshot)

            suffix = "persistent_unavailable" if consecutive >= 2 else "unavailable"
            insights.append(
                self.normalizer.normalize(
                    kind="observation_coverage",
                    subject=signal,
                    status="present",
                    key_parts=[signal, suffix],
                    evidence={"signal": signal, "consecutive_snapshots": consecutive},
                    first_seen_at=matching[-1].observed_at,
                    last_seen_at=matching[0].observed_at,
                )
            )
        return insights
