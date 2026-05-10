# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Lifecycle models — stages, policy, per-stage I/O."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TaskLifecycleStage(str, Enum):
    """v1 stages. Add only when each new stage has explicit I/O records."""

    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"


class LifecycleStagePolicy(str, Enum):
    """How the runner advances when a stage finishes.

    Intentionally excludes ``manual_gate_between_stages`` — no concrete
    gate mechanism is defined in v1.
    """

    STOP_ON_FIRST_FAILURE = "stop_on_first_failure"
    RUN_ALL_BEST_EFFORT = "run_all_best_effort"


class StageStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Stage I/O
# ---------------------------------------------------------------------------


class Check(BaseModel):
    """A single verification check declared by the plan stage and consumed
    by the verify stage. ``check_id`` is the stable identifier."""

    check_id: str
    description: Optional[str] = None
    model_config = {"frozen": True}


class CheckResult(BaseModel):
    check_id: str
    passed: bool
    reason: Optional[str] = None
    model_config = {"frozen": True}


class PlanOutput(BaseModel):
    plan_summary: str
    target_repos: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    # The verify stage consumes this list verbatim. Plan declares; verify
    # confirms. ER-003 micro-note resolution: checks are emitted by plan.
    checks: list[Check] = Field(default_factory=list)
    model_config = {"frozen": True}


class ExecuteOutput(BaseModel):
    result_ref: str = Field(description="run_id or pointer to the ExecutionResult")
    status: str = Field(description="status string from the underlying execution")
    error: Optional[str] = None
    model_config = {"frozen": True}


class VerifyOutput(BaseModel):
    verification_status: str = Field(
        description="'pass' if all checks passed, else 'fail'"
    )
    checks: list[CheckResult] = Field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Per-stage report and lifecycle metadata / outcome
# ---------------------------------------------------------------------------


class StageReport(BaseModel):
    stage: TaskLifecycleStage
    status: StageStatus
    error: Optional[str] = None
    model_config = {"frozen": True}


class LifecycleMetadata(BaseModel):
    """Optional ``ExecutionRequest`` field. Drives the lifecycle runner
    when present; absence keeps one-shot execution behavior unchanged."""

    requested_stages: list[TaskLifecycleStage] = Field(
        default_factory=lambda: [
            TaskLifecycleStage.PLAN,
            TaskLifecycleStage.EXECUTE,
            TaskLifecycleStage.VERIFY,
        ]
    )
    stage_policy: LifecycleStagePolicy = LifecycleStagePolicy.STOP_ON_FIRST_FAILURE
    model_config = {"frozen": True}


class LifecycleOutcome(BaseModel):
    """What ``ExecutionResult`` reports back about lifecycle execution."""

    completed_stages: list[TaskLifecycleStage] = Field(default_factory=list)
    failed_stages: list[TaskLifecycleStage] = Field(default_factory=list)
    skipped_stages: list[TaskLifecycleStage] = Field(default_factory=list)
    reports: list[StageReport] = Field(default_factory=list)
    model_config = {"frozen": True}
