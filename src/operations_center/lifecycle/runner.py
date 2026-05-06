# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Lifecycle runner — drives plan/execute/verify stages with deterministic policy.

The runner is backend-agnostic and never calls a real LLM. Stage handlers
are passed in via ``StageHandlers`` so tests run with simple callables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

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


# ---------------------------------------------------------------------------
# Handler protocols
# ---------------------------------------------------------------------------


class PlanHandler(Protocol):
    def __call__(self, *, request, repo_graph_context: object | None) -> PlanOutput: ...


class ExecuteHandler(Protocol):
    def __call__(self, *, request, plan: PlanOutput) -> ExecuteOutput: ...


class VerifyHandler(Protocol):
    def __call__(
        self, *, request, plan: PlanOutput, execution: ExecuteOutput
    ) -> list[CheckResult]: ...


@dataclass
class StageHandlers:
    plan: PlanHandler
    execute: ExecuteHandler
    verify: VerifyHandler


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class LifecycleResult:
    plan: PlanOutput | None
    execute: ExecuteOutput | None
    verify: VerifyOutput | None
    outcome: LifecycleOutcome


class LifecycleRunner:
    def __init__(self, handlers: StageHandlers) -> None:
        self._handlers = handlers

    def run(
        self,
        *,
        request,
        metadata: LifecycleMetadata,
        repo_graph_context: object | None = None,
    ) -> LifecycleResult:
        completed: list[TaskLifecycleStage] = []
        failed: list[TaskLifecycleStage] = []
        skipped: list[TaskLifecycleStage] = []
        reports: list[StageReport] = []
        plan_out: PlanOutput | None = None
        execute_out: ExecuteOutput | None = None
        verify_out: VerifyOutput | None = None
        already_failed = False

        for stage in metadata.requested_stages:
            if already_failed and metadata.stage_policy == LifecycleStagePolicy.STOP_ON_FIRST_FAILURE:
                skipped.append(stage)
                reports.append(StageReport(stage=stage, status=StageStatus.SKIPPED))
                continue

            try:
                if stage == TaskLifecycleStage.PLAN:
                    plan_out = self._handlers.plan(
                        request=request, repo_graph_context=repo_graph_context
                    )
                elif stage == TaskLifecycleStage.EXECUTE:
                    if plan_out is None:
                        raise RuntimeError(
                            "execute stage requires plan stage to have run"
                        )
                    execute_out = self._handlers.execute(request=request, plan=plan_out)
                elif stage == TaskLifecycleStage.VERIFY:
                    if plan_out is None or execute_out is None:
                        raise RuntimeError(
                            "verify stage requires both plan and execute stages"
                        )
                    check_results = self._handlers.verify(
                        request=request, plan=plan_out, execution=execute_out
                    )
                    verify_out = _build_verify_output(plan_out.checks, check_results)
                    if verify_out.verification_status != "pass":
                        raise _StageFailure("verification failed")
                else:
                    raise RuntimeError(f"unknown stage: {stage}")
                completed.append(stage)
                reports.append(StageReport(stage=stage, status=StageStatus.SUCCEEDED))
            except _StageFailure as exc:
                failed.append(stage)
                reports.append(
                    StageReport(stage=stage, status=StageStatus.FAILED, error=str(exc))
                )
                already_failed = True
            except Exception as exc:
                failed.append(stage)
                reports.append(
                    StageReport(stage=stage, status=StageStatus.FAILED, error=str(exc))
                )
                already_failed = True

        outcome = LifecycleOutcome(
            completed_stages=completed,
            failed_stages=failed,
            skipped_stages=skipped,
            reports=reports,
        )
        return LifecycleResult(
            plan=plan_out, execute=execute_out, verify=verify_out, outcome=outcome
        )


class _StageFailure(Exception):
    """Internal signal — a stage's typed failure (vs. unexpected exception)."""


def _build_verify_output(
    declared: list[Check], results: list[CheckResult]
) -> VerifyOutput:
    """Validate that verify reports cover the plan's declared checks.

    Plan declares the check_ids; verify must produce a CheckResult per
    declared check. Missing check_ids become implicit failures so the
    plan→verify contract is enforced deterministically.
    """
    by_id = {r.check_id: r for r in results}
    materialized: list[CheckResult] = []
    for check in declared:
        if check.check_id in by_id:
            materialized.append(by_id[check.check_id])
        else:
            materialized.append(
                CheckResult(
                    check_id=check.check_id,
                    passed=False,
                    reason="check not reported by verify handler",
                )
            )
    all_passed = all(c.passed for c in materialized)
    return VerifyOutput(
        verification_status="pass" if all_passed else "fail",
        checks=materialized,
    )
