# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot


class LintDriftDeriver:
    """Derive lint-related insights from observer snapshots.

    Fires on:
    - lint_violations_present: current snapshot has ruff violations.
    - lint_violations_worsened: violation count increased since last snapshot.
    """

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []

        current_lint = snapshots[0].signals.lint_signal
        insights: list[DerivedInsight] = []

        if current_lint.status == "unavailable":
            return []

        if current_lint.status == "violations" and current_lint.violation_count > 0:
            top_codes: list[str] = []
            seen: set[str] = set()
            for v in current_lint.top_violations:
                if v.code not in seen:
                    top_codes.append(v.code)
                    seen.add(v.code)
                if len(top_codes) >= 5:
                    break

            distinct_file_count = current_lint.distinct_file_count or len({v.path for v in current_lint.top_violations})
            insights.append(
                self.normalizer.normalize(
                    kind="lint_drift",
                    subject="lint_violations",
                    status="present",
                    key_parts=["lint_violations", "present"],
                    evidence={
                        "violation_count": current_lint.violation_count,
                        "distinct_file_count": distinct_file_count,
                        "top_codes": top_codes,
                        "source": current_lint.source or "ruff",
                    },
                    first_seen_at=snapshots[0].observed_at,
                    last_seen_at=snapshots[0].observed_at,
                )
            )

        if len(snapshots) > 1:
            previous_lint = snapshots[1].signals.lint_signal
            if (
                previous_lint.status != "unavailable"
                and current_lint.violation_count > previous_lint.violation_count
            ):
                delta = current_lint.violation_count - previous_lint.violation_count
                insights.append(
                    self.normalizer.normalize(
                        kind="lint_drift",
                        subject="lint_violations",
                        status="worsened",
                        key_parts=["lint_violations", "worsened"],
                        evidence={
                            "current_count": current_lint.violation_count,
                            "previous_count": previous_lint.violation_count,
                            "delta": delta,
                            "distinct_file_count": (
                                current_lint.distinct_file_count
                                or len({v.path for v in current_lint.top_violations})
                            ),
                        },
                        first_seen_at=snapshots[1].observed_at,
                        last_seen_at=snapshots[0].observed_at,
                    )
                )

        return insights
