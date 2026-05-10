# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot


class TypeHealthDeriver:
    """Derive type-check insights from observer snapshots.

    Fires on:
    - type_errors_present: current snapshot has type errors.
    - type_errors_worsened: error count increased since last snapshot.
    """

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []

        current_type = snapshots[0].signals.type_signal
        if current_type.status == "unavailable":
            return []

        insights: list[DerivedInsight] = []

        if current_type.status == "errors" and current_type.error_count > 0:
            top_codes: list[str] = []
            seen: set[str] = set()
            for e in current_type.top_errors:
                if e.code and e.code not in seen:
                    top_codes.append(e.code)
                    seen.add(e.code)
                if len(top_codes) >= 5:
                    break

            distinct_file_count = current_type.distinct_file_count or len({e.path for e in current_type.top_errors})
            insights.append(
                self.normalizer.normalize(
                    kind="type_health",
                    subject="type_errors",
                    status="present",
                    key_parts=["type_errors", "present"],
                    evidence={
                        "error_count": current_type.error_count,
                        "distinct_file_count": distinct_file_count,
                        "top_codes": top_codes,
                        "source": current_type.source or "unknown",
                    },
                    first_seen_at=snapshots[0].observed_at,
                    last_seen_at=snapshots[0].observed_at,
                )
            )

        if len(snapshots) > 1:
            previous_type = snapshots[1].signals.type_signal
            if (
                previous_type.status != "unavailable"
                and current_type.error_count > previous_type.error_count
            ):
                delta = current_type.error_count - previous_type.error_count
                insights.append(
                    self.normalizer.normalize(
                        kind="type_health",
                        subject="type_errors",
                        status="worsened",
                        key_parts=["type_errors", "worsened"],
                        evidence={
                            "current_count": current_type.error_count,
                            "previous_count": previous_type.error_count,
                            "delta": delta,
                            "distinct_file_count": (
                                current_type.distinct_file_count
                                or len({e.path for e in current_type.top_errors})
                            ),
                        },
                        first_seen_at=snapshots[1].observed_at,
                        last_seen_at=snapshots[0].observed_at,
                    )
                )

        return insights
