# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot

# Minimum runs before we trust the rates enough to derive an insight.
_MIN_RUNS_FOR_RATE = 5
# Fraction of runs that are no_ops before we flag it.
_HIGH_NO_OP_RATE_THRESHOLD = 0.5
# Absolute count of validation failures before we flag it.
_DEFAULT_VALIDATION_FAILURE_THRESHOLD = 2


class ExecutionHealthDeriver:
    """Derives execution-health insights from retained execution artifacts.

    Signals:
    - high_no_op_rate: most recent runs for this repo produced no code changes,
      suggesting tasks are being generated but kodo cannot act on them.
    - persistent_validation_failures: multiple executed runs failed validation,
      suggesting a systemic quality issue or overly complex task descriptions.
    - repeated_unknown_failures: multiple runs ended with unknown or error outcomes,
      suggesting the repo is producing repeated unexplained failures.
    """

    def __init__(
        self,
        normalizer: InsightNormalizer,
        validation_failure_threshold: int = 2,
        unknown_failure_threshold: int = 2,
    ) -> None:
        self.normalizer = normalizer
        self.validation_failure_threshold = validation_failure_threshold
        self.unknown_failure_threshold = unknown_failure_threshold

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        current = snapshots[0]
        sig = current.signals.execution_health
        repo_name = current.repo.name
        insights: list[DerivedInsight] = []

        if sig.total_runs >= _MIN_RUNS_FOR_RATE:
            no_op_rate = sig.no_op_count / sig.total_runs
            if no_op_rate >= _HIGH_NO_OP_RATE_THRESHOLD:
                insights.append(
                    self.normalizer.normalize(
                        kind="execution_health",
                        subject=repo_name,
                        status="present",
                        key_parts=[repo_name, "high_no_op_rate"],
                        evidence={
                            "repo": repo_name,
                            "total_runs": sig.total_runs,
                            "no_op_count": sig.no_op_count,
                            "no_op_rate": round(no_op_rate, 2),
                            "pattern": "high_no_op_rate",
                        },
                        first_seen_at=current.observed_at,
                        last_seen_at=current.observed_at,
                    )
                )

        unknown_error_total = sig.unknown_count + sig.error_count
        if unknown_error_total >= self.unknown_failure_threshold:
            insights.append(
                self.normalizer.normalize(
                    kind="execution_health",
                    subject=repo_name,
                    status="present",
                    key_parts=[repo_name, "repeated_unknown_failures"],
                    evidence={
                        "repo": repo_name,
                        "total_runs": sig.total_runs,
                        "unknown_count": sig.unknown_count,
                        "error_count": sig.error_count,
                        "unknown_error_total": unknown_error_total,
                        "pattern": "repeated_unknown_failures",
                    },
                    first_seen_at=current.observed_at,
                    last_seen_at=current.observed_at,
                )
            )

        if sig.validation_failed_count >= self.validation_failure_threshold:
            insights.append(
                self.normalizer.normalize(
                    kind="execution_health",
                    subject=repo_name,
                    status="present",
                    key_parts=[repo_name, "persistent_validation_failures"],
                    evidence={
                        "repo": repo_name,
                        "total_runs": sig.total_runs,
                        "executed_count": sig.executed_count,
                        "validation_failed_count": sig.validation_failed_count,
                        "pattern": "persistent_validation_failures",
                    },
                    first_seen_at=current.observed_at,
                    last_seen_at=current.observed_at,
                )
            )

        return insights
