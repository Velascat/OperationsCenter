from __future__ import annotations

from collections.abc import Sequence

from control_plane.insights.models import DerivedInsight
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import RepoStateSnapshot


class FileHotspotsDeriver:
    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        current = snapshots[0]
        hotspots = current.signals.file_hotspots
        if not hotspots:
            return []
        insights: list[DerivedInsight] = []
        top = hotspots[0]
        other_total = sum(item.touch_count for item in hotspots[1:])
        if top.touch_count > 0 and top.touch_count >= max(2, other_total):
            insights.append(
                self.normalizer.normalize(
                    kind="file_hotspot",
                    subject=top.path,
                    status="present",
                    key_parts=[top.path, "dominant_current"],
                    evidence={
                        "current_touch_count": top.touch_count,
                        "other_touch_count_total": other_total,
                    },
                    first_seen_at=current.observed_at,
                    last_seen_at=current.observed_at,
                )
            )
        for hotspot in hotspots:
            appearances = [snapshot for snapshot in snapshots if any(item.path == hotspot.path for item in snapshot.signals.file_hotspots)]
            if len(appearances) < 2:
                continue
            insights.append(
                self.normalizer.normalize(
                    kind="file_hotspot",
                    subject=hotspot.path,
                    status="present",
                    key_parts=[hotspot.path, "repeated_presence"],
                    evidence={
                        "current_touch_count": hotspot.touch_count,
                        "appears_in_recent_snapshots": len(appearances),
                    },
                    first_seen_at=appearances[-1].observed_at,
                    last_seen_at=appearances[0].observed_at,
                )
            )
        return insights
