from __future__ import annotations

from collections.abc import Sequence

from control_plane.insights.models import DerivedInsight
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import RepoStateSnapshot


class ValidationPatternDeriver:
    """Derive validation-failure-pattern insights from observer snapshots.

    Fires when one or more tasks have been executed multiple times and failed
    post-execution validation repeatedly — a signal that a specific task is
    systematically difficult or broken.
    """

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []

        current = snapshots[0].signals.validation_history
        if current.status == "unavailable":
            return []

        insights: list[DerivedInsight] = []

        if current.status == "patterns_detected" and current.tasks_with_repeated_failures:
            top = current.tasks_with_repeated_failures[:3]
            top_task_ids = [r.task_id for r in top]
            top_roles = list({r.worker_role for r in top})
            worst = top[0]

            insights.append(
                self.normalizer.normalize(
                    kind="validation_pattern",
                    subject="execution_tasks",
                    status="repeated_failures",
                    key_parts=["execution_tasks", "repeated_failures"],
                    evidence={
                        "tasks_with_repeated_failures": len(current.tasks_with_repeated_failures),
                        "top_task_ids": top_task_ids,
                        "top_worker_roles": top_roles,
                        "worst_task_id": worst.task_id,
                        "worst_task_failure_count": worst.validation_failure_count,
                        "worst_task_total_runs": worst.total_runs,
                        "overall_failure_rate": current.overall_failure_rate,
                        "tasks_analyzed": current.tasks_analyzed,
                    },
                    first_seen_at=snapshots[0].observed_at,
                    last_seen_at=snapshots[0].observed_at,
                )
            )

        return insights
