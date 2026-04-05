from __future__ import annotations

from collections.abc import Sequence

from control_plane.insights.models import DerivedInsight
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import RepoStateSnapshot


class CIPatternDeriver:
    """Derive CI health insights from observer snapshots.

    Fires on:
    - ci_pattern/failing: one or more checks consistently failing in recent commits.
    - ci_pattern/flaky: one or more checks show intermittent failures.
    """

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []

        current_ci = snapshots[0].signals.ci_history
        if current_ci.status == "unavailable":
            return []

        insights: list[DerivedInsight] = []

        if current_ci.failing_checks:
            insights.append(
                self.normalizer.normalize(
                    kind="ci_pattern",
                    subject="ci_checks",
                    status="failing",
                    key_parts=["ci_checks", "failing"],
                    evidence={
                        "failing_checks": current_ci.failing_checks,
                        "failure_rate": current_ci.failure_rate,
                        "runs_checked": current_ci.runs_checked,
                        "source": current_ci.source or "github_checks",
                    },
                    first_seen_at=snapshots[0].observed_at,
                    last_seen_at=snapshots[0].observed_at,
                )
            )

        if current_ci.flaky_checks:
            insights.append(
                self.normalizer.normalize(
                    kind="ci_pattern",
                    subject="ci_checks",
                    status="flaky",
                    key_parts=["ci_checks", "flaky"],
                    evidence={
                        "flaky_checks": current_ci.flaky_checks,
                        "failure_rate": current_ci.failure_rate,
                        "runs_checked": current_ci.runs_checked,
                        "source": current_ci.source or "github_checks",
                    },
                    first_seen_at=snapshots[0].observed_at,
                    last_seen_at=snapshots[0].observed_at,
                )
            )

        return insights
