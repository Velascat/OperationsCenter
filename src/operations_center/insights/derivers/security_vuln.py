from __future__ import annotations

from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot


class SecurityVulnDeriver:
    """Derive security vulnerability insights from observer snapshots.

    Fires on:
    - security_vuln/present: security signal reports advisories.
    """

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []

        sec = snapshots[0].signals.security_signal
        if sec.status == "unavailable":
            return []

        insights: list[DerivedInsight] = []
        observed_at = snapshots[0].observed_at

        if sec.status == "advisories" and sec.advisory_count > 0:
            insights.append(
                self.normalizer.normalize(
                    kind="security_vuln",
                    subject="security",
                    status="advisories",
                    key_parts=["present"],
                    evidence={
                        "advisory_count": sec.advisory_count,
                        "critical_count": sec.critical_count,
                        "high_count": sec.high_count,
                        "summary": sec.summary or "",
                    },
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                )
            )

        return insights
