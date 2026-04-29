# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot


class TestContinuityDeriver:
    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        """Derive continuity insights from consecutive test signal statuses.

        Tracks all statuses including 'passed', 'failed', 'unknown',
        'discoverable', and 'no_config'.  Emits a ``persistent`` insight
        when the same status repeats for >= 2 snapshots and a ``transition``
        insight whenever the status changes between adjacent snapshots.
        """
        if not snapshots:
            return []
        current_status = snapshots[0].signals.test_signal.status
        consecutive = 0
        consecutive_snapshots: list[RepoStateSnapshot] = []
        for snapshot in snapshots:
            if snapshot.signals.test_signal.status != current_status:
                break
            consecutive += 1
            consecutive_snapshots.append(snapshot)

        insights: list[DerivedInsight] = []
        if consecutive >= 2:
            insights.append(
                self.normalizer.normalize(
                    kind="test_status_continuity",
                    subject="test_signal",
                    status="present",
                    key_parts=[current_status, "persistent"],
                    evidence={
                        "current_status": current_status,
                        "consecutive_snapshots": consecutive,
                    },
                    first_seen_at=consecutive_snapshots[-1].observed_at,
                    last_seen_at=consecutive_snapshots[0].observed_at,
                )
            )
        if len(snapshots) > 1:
            previous_status = snapshots[1].signals.test_signal.status
            if previous_status != current_status:
                insights.append(
                    self.normalizer.normalize(
                        kind="test_status_continuity",
                        subject="test_signal",
                        status="present",
                        key_parts=[previous_status, current_status, "transition"],
                        evidence={
                            "previous_status": previous_status,
                            "current_status": current_status,
                        },
                        first_seen_at=snapshots[1].observed_at,
                        last_seen_at=snapshots[0].observed_at,
                    )
                )
        return insights
