# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import re
from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.collectors.backlog import promotable_items
from operations_center.observer.models import RepoStateSnapshot


def _slug(title: str) -> str:
    """Convert a backlog title to a stable dedup-key-safe slug."""
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60]


class BacklogPromotionDeriver:
    """Emits one insight per promotable backlog item in the most recent snapshot.

    Items typed `arch` or `redesign` are never emitted — those require deliberate
    operator action and are excluded at the collector level.
    """

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        current = snapshots[0]
        repo_name = current.repo.name
        items = promotable_items(current.signals.backlog)
        insights: list[DerivedInsight] = []
        for item in items:
            insights.append(
                self.normalizer.normalize(
                    kind="backlog_item",
                    subject=item.title,
                    status="pending",
                    key_parts=[repo_name, _slug(item.title)],
                    evidence={
                        "repo": repo_name,
                        "title": item.title,
                        "item_type": item.item_type,
                        "description": item.description,
                    },
                    first_seen_at=current.observed_at,
                    last_seen_at=current.observed_at,
                )
            )
        return insights
