# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionControlSettings:
    max_exec_per_hour: int
    max_exec_per_day: int
    max_retries_per_task: int
    min_watch_interval_seconds: int
    min_remaining_exec_for_proposals: int
    usage_path: Path

    pr_dry_run: bool = False

    @classmethod
    def from_env(cls) -> "ExecutionControlSettings":
        return cls(
            max_exec_per_hour=max(0, int(os.environ.get("OPERATIONS_CENTER_MAX_EXEC_PER_HOUR", "10"))),
            max_exec_per_day=max(0, int(os.environ.get("OPERATIONS_CENTER_MAX_EXEC_PER_DAY", "50"))),
            max_retries_per_task=max(1, int(os.environ.get("OPERATIONS_CENTER_MAX_RETRIES_PER_TASK", "3"))),
            min_watch_interval_seconds=max(1, int(os.environ.get("OPERATIONS_CENTER_MIN_WATCH_INTERVAL_SECONDS", "5"))),
            min_remaining_exec_for_proposals=max(
                0,
                int(os.environ.get("OPERATIONS_CENTER_MIN_REMAINING_EXEC_FOR_PROPOSALS", "3")),
            ),
            usage_path=Path(
                os.environ.get(
                    "OPERATIONS_CENTER_EXECUTION_USAGE_PATH",
                    "tools/report/operations_center/execution/usage.json",
                )
            ),
            pr_dry_run=os.environ.get("OPERATIONS_CENTER_PR_DRY_RUN", "0").strip() in ("1", "true", "yes"),
        )


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str | None = None
    window: str | None = None
    limit: int | None = None
    current: int | None = None


@dataclass(frozen=True)
class RetryDecision:
    allowed: bool
    reason: str | None = None
    attempts: int = 0
    limit: int = 0


@dataclass(frozen=True)
class NoOpDecision:
    should_skip: bool
    reason: str | None = None
    detail: str | None = None
