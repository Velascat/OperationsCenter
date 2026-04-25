from __future__ import annotations

from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot


class CommitActivityDeriver:
    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        current = snapshots[0]
        current_count = len(current.signals.recent_commits)
        current_authors = len({commit.author for commit in current.signals.recent_commits})
        insights = [
            self.normalizer.normalize(
                kind="commit_activity",
                subject=current.repo.name,
                status="present",
                key_parts=["recent_window"],
                evidence={
                    "current_commit_count": current_count,
                    "active_authors_count": current_authors,
                },
                first_seen_at=snapshots[-1].observed_at,
                last_seen_at=current.observed_at,
            )
        ]
        if len(snapshots) > 1:
            previous_count = len(snapshots[1].signals.recent_commits)
            if previous_count != current_count:
                insights.append(
                    self.normalizer.normalize(
                        kind="commit_activity",
                        subject=current.repo.name,
                        status="present",
                        key_parts=["recent_window_changed"],
                        evidence={
                            "current_commit_count": current_count,
                            "previous_commit_count": previous_count,
                        },
                        first_seen_at=snapshots[1].observed_at,
                        last_seen_at=current.observed_at,
                    )
                )
        return insights
