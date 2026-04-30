# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot


class ArchitectureDriftDeriver:
    """Derive architecture-related insights from observer snapshots.

    Fires on:
    - arch_drift/coupling_high: coupling_score >= 0.7
    - arch_drift/module_bloat: max_import_depth >= 6
    """

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []

        arch = snapshots[0].signals.architecture_signal
        if arch.status == "unavailable":
            return []

        insights: list[DerivedInsight] = []
        observed_at = snapshots[0].observed_at

        if arch.coupling_score is not None and arch.coupling_score >= 0.7:
            insights.append(
                self.normalizer.normalize(
                    kind="arch_drift",
                    subject="coupling",
                    status="high",
                    key_parts=["coupling_high"],
                    evidence={
                        "coupling_score": arch.coupling_score,
                        "circular_dependencies": arch.circular_dependencies,
                        "summary": arch.summary or "",
                    },
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                )
            )

        if arch.max_import_depth is not None and arch.max_import_depth >= 6:
            insights.append(
                self.normalizer.normalize(
                    kind="arch_drift",
                    subject="module_depth",
                    status="bloated",
                    key_parts=["module_bloat"],
                    evidence={
                        "max_import_depth": arch.max_import_depth,
                        "summary": arch.summary or "",
                    },
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                )
            )

        return insights
