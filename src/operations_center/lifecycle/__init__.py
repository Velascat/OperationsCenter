# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ER-003 — Task lifecycle primitive.

Minimal lifecycle stages with concrete I/O:

  plan    → PlanOutput (plan_summary, target_repos, steps, checks)
  execute → ExecuteOutput (result_ref, status)
  verify  → VerifyOutput (verification_status, checks[], failures)

Lifecycle metadata is **optional** on ``ExecutionRequest``. Existing
one-shot execution (no ``lifecycle`` field) is unchanged.
"""

from .models import (
    Check,
    CheckResult,
    ExecuteOutput,
    LifecycleMetadata,
    LifecycleOutcome,
    LifecycleStagePolicy,
    PlanOutput,
    StageReport,
    StageStatus,
    TaskLifecycleStage,
    VerifyOutput,
)
from .runner import LifecycleRunner, StageHandlers

__all__ = [
    "Check",
    "CheckResult",
    "ExecuteOutput",
    "LifecycleMetadata",
    "LifecycleOutcome",
    "LifecycleRunner",
    "LifecycleStagePolicy",
    "PlanOutput",
    "StageHandlers",
    "StageReport",
    "StageStatus",
    "TaskLifecycleStage",
    "VerifyOutput",
]
