from __future__ import annotations

from collections.abc import Sequence

from control_plane.insights.models import DerivedInsight
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import RepoStateSnapshot


class DirtyTreeDeriver:
    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        current = snapshots[0]
        if not current.repo.is_dirty:
            return []
        dirty_snapshots = [snapshot for snapshot in snapshots if snapshot.repo.is_dirty]
        return [
            self.normalizer.normalize(
                kind="dirty_tree",
                subject="working_tree",
                status="present",
                key_parts=["working_tree", "dirty"],
                evidence={"is_dirty": True, "dirty_snapshots": len(dirty_snapshots)},
                first_seen_at=dirty_snapshots[-1].observed_at,
                last_seen_at=dirty_snapshots[0].observed_at,
            )
        ]
