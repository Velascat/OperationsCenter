from __future__ import annotations

from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot


class DependencyDriftDeriver:
    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        current_status = snapshots[0].signals.dependency_drift.status
        insights: list[DerivedInsight] = []
        if current_status == "available":
            available_snapshots = [snapshot for snapshot in snapshots if snapshot.signals.dependency_drift.status == "available"]
            insights.append(
                self.normalizer.normalize(
                    kind="dependency_drift_continuity",
                    subject="dependency_drift",
                    status="present",
                    key_parts=["present", "current"],
                    evidence={"current_status": current_status},
                    first_seen_at=available_snapshots[-1].observed_at,
                    last_seen_at=available_snapshots[0].observed_at,
                )
            )
            if len(available_snapshots) >= 2:
                insights.append(
                    self.normalizer.normalize(
                        kind="dependency_drift_continuity",
                        subject="dependency_drift",
                        status="present",
                        key_parts=["present", "persistent"],
                        evidence={"consecutive_snapshots": len(available_snapshots)},
                        first_seen_at=available_snapshots[-1].observed_at,
                        last_seen_at=available_snapshots[0].observed_at,
                    )
                )
        if len(snapshots) > 1 and current_status != snapshots[1].signals.dependency_drift.status and current_status == "not_available":
            insights.append(
                self.normalizer.normalize(
                    kind="dependency_drift_continuity",
                    subject="dependency_drift",
                    status="present",
                    key_parts=["not_available", "transition"],
                    evidence={
                        "previous_status": snapshots[1].signals.dependency_drift.status,
                        "current_status": current_status,
                    },
                    first_seen_at=snapshots[1].observed_at,
                    last_seen_at=snapshots[0].observed_at,
                )
            )
        return insights
