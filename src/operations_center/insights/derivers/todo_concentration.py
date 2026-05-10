# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot


class TodoConcentrationDeriver:
    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        current = snapshots[0].signals.todo_signal
        insights: list[DerivedInsight] = []
        total = current.todo_count + current.fixme_count
        if current.fixme_count > 0:
            insights.append(
                self.normalizer.normalize(
                    kind="todo_concentration",
                    subject="fixme",
                    status="present",
                    key_parts=["fixme", "present"],
                    evidence={"fixme_count": current.fixme_count},
                    first_seen_at=snapshots[0].observed_at,
                    last_seen_at=snapshots[0].observed_at,
                )
            )
        if current.top_files and total > 0:
            top = current.top_files[0]
            if top.count / total >= 0.5:
                insights.append(
                    self.normalizer.normalize(
                        kind="todo_concentration",
                        subject=top.path,
                        status="present",
                        key_parts=[top.path, "top_file_concentration"],
                        evidence={"current_count": top.count, "total_markers": total},
                        first_seen_at=snapshots[0].observed_at,
                        last_seen_at=snapshots[0].observed_at,
                    )
                )
        if len(snapshots) > 1:
            previous = snapshots[1].signals.todo_signal
            previous_total = previous.todo_count + previous.fixme_count
            if previous_total != total:
                insights.append(
                    self.normalizer.normalize(
                        kind="todo_concentration",
                        subject="todo_fixme_total",
                        status="present",
                        key_parts=["todo_fixme_total", "count_changed"],
                        evidence={"current_total": total, "previous_total": previous_total},
                        first_seen_at=snapshots[1].observed_at,
                        last_seen_at=snapshots[0].observed_at,
                    )
                )
        return insights
