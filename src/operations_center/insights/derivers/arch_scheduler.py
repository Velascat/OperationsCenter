# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import re
from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot
from operations_center.tuning.loader import TuningArtifactLoader


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60]


# --- health thresholds -------------------------------------------------------

# Minimum execution runs before we trust the health signal.
_MIN_RUNS = 10
# Maximum acceptable no-op rate (kodo ran but made no changes).
_MAX_NO_OP_RATE = 0.30
# Validation failures must be zero — arch work on a broken baseline is unsafe.
_MAX_VALIDATION_FAILURES = 0

# Tuning families that must all show "keep" before arch work is scheduled.
_REQUIRED_STABLE_FAMILIES = frozenset({
    "observation_coverage",
    "test_visibility",
    "dependency_drift",
})

# Types treated as arch-class (never auto-promoted by the regular backlog family).
_ARCH_TYPES: frozenset[str] = frozenset({"arch", "redesign"})


class ArchSchedulerDeriver:
    """Promotes arch/redesign backlog items only when health metrics are stable.

    Gates:
    1. execution_health.total_runs >= _MIN_RUNS
    2. no_op_rate < _MAX_NO_OP_RATE
    3. validation_failed_count == 0
    4. Latest tune-autonomy run shows "keep" for all default families
       (if no tuning artifact exists, the gate fails closed).

    All gates must pass. If any gate fails, no insights are emitted and
    the reason is recorded in the evidence of a single "blocked" insight
    so the operator can see why arch work isn't scheduled yet.
    """

    def __init__(
        self,
        normalizer: InsightNormalizer,
        tuning_loader: TuningArtifactLoader | None = None,
    ) -> None:
        self.normalizer = normalizer
        self.tuning_loader = tuning_loader or TuningArtifactLoader()

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        current = snapshots[0]
        repo_name = current.repo.name

        arch_items = [
            item for item in current.signals.backlog.items
            if item.item_type in _ARCH_TYPES
        ]
        if not arch_items:
            return []

        blocked, reasons = self._health_check(current)
        if blocked:
            # Emit a single "not ready" insight so the operator can see the gate state.
            return [
                self.normalizer.normalize(
                    kind="arch_schedule_blocked",
                    subject=repo_name,
                    status="blocked",
                    key_parts=[repo_name, "arch_schedule_blocked"],
                    evidence={
                        "repo": repo_name,
                        "reasons": reasons,
                        "pending_items": [item.title for item in arch_items],
                    },
                    first_seen_at=current.observed_at,
                    last_seen_at=current.observed_at,
                )
            ]

        insights: list[DerivedInsight] = []
        for item in arch_items:
            insights.append(
                self.normalizer.normalize(
                    kind="arch_backlog_item",
                    subject=item.title,
                    status="ready",
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

    def _health_check(self, snapshot: RepoStateSnapshot) -> tuple[bool, list[str]]:
        """Return (blocked, reasons). blocked=True means gates did not pass."""
        reasons: list[str] = []
        sig = snapshot.signals.execution_health

        if sig.total_runs < _MIN_RUNS:
            reasons.append(
                f"insufficient execution history: {sig.total_runs} runs, need {_MIN_RUNS}"
            )

        if sig.total_runs > 0:
            no_op_rate = sig.no_op_count / sig.total_runs
            if no_op_rate >= _MAX_NO_OP_RATE:
                reasons.append(
                    f"no-op rate too high: {no_op_rate:.0%} (threshold {_MAX_NO_OP_RATE:.0%})"
                )

        if sig.validation_failed_count > _MAX_VALIDATION_FAILURES:
            reasons.append(
                f"validation failures present: {sig.validation_failed_count} recent failures"
            )

        tuning_reasons = self._check_tuning_stability()
        reasons.extend(tuning_reasons)

        return bool(reasons), reasons

    def _check_tuning_stability(self) -> list[str]:
        """Check that the latest tune-autonomy run shows 'keep' for required families.
        Fail closed if no tuning artifacts exist."""
        artifacts = self.tuning_loader.load_recent(limit=1)
        if not artifacts:
            return ["no tune-autonomy run on record — run tune-autonomy first"]

        latest = artifacts[0]
        by_family = {r.family: r.action for r in latest.recommendations}
        unstable: list[str] = []
        for family in _REQUIRED_STABLE_FAMILIES:
            action = by_family.get(family)
            if action is None:
                unstable.append(f"{family}: no recommendation in latest tuning run")
            elif action != "keep":
                unstable.append(f"{family}: recommendation is '{action}', need 'keep'")

        if unstable:
            return [f"tuning not stable: {'; '.join(unstable)}"]
        return []
