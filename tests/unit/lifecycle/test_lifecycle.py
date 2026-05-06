# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ER-003 — Lifecycle primitive tests.

No live LLM calls. Stage handlers are simple in-test callables.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.contracts.enums import ExecutionStatus
from operations_center.lifecycle import (
    Check,
    CheckResult,
    ExecuteOutput,
    LifecycleMetadata,
    LifecycleOutcome,
    LifecycleRunner,
    LifecycleStagePolicy,
    PlanOutput,
    StageHandlers,
    StageStatus,
    TaskLifecycleStage,
    VerifyOutput,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(lifecycle: LifecycleMetadata | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        proposal_id="p",
        decision_id="d",
        goal_text="g",
        repo_key="velascat/x",
        clone_url="https://example.invalid/x.git",
        base_branch="main",
        task_branch="b",
        workspace_path=Path("/tmp/x"),
        lifecycle=lifecycle,
    )


def _ok_plan(*, request, repo_graph_context) -> PlanOutput:
    return PlanOutput(
        plan_summary="ok plan",
        target_repos=["velascat/x"],
        steps=["edit file", "run tests"],
        checks=[Check(check_id="tests_pass"), Check(check_id="diff_under_limit")],
    )


def _ok_execute(*, request, plan: PlanOutput) -> ExecuteOutput:
    return ExecuteOutput(result_ref="run-1", status="succeeded")


def _all_pass_verify(*, request, plan, execution) -> list[CheckResult]:
    return [CheckResult(check_id=c.check_id, passed=True) for c in plan.checks]


def _failing_verify(*, request, plan, execution) -> list[CheckResult]:
    return [
        CheckResult(check_id="tests_pass", passed=True),
        CheckResult(check_id="diff_under_limit", passed=False, reason="too big"),
    ]


def _exec_raises(*, request, plan):  # type: ignore[no-untyped-def]
    raise RuntimeError("backend died")


# ---------------------------------------------------------------------------
# Contract integration — optional metadata, backwards compatible
# ---------------------------------------------------------------------------


class TestContractIntegration:
    def test_request_without_lifecycle_remains_valid(self) -> None:
        req = _make_request()
        assert req.lifecycle is None

    def test_request_with_lifecycle_metadata(self) -> None:
        req = _make_request(LifecycleMetadata())
        assert req.lifecycle is not None
        assert req.lifecycle.requested_stages == [
            TaskLifecycleStage.PLAN,
            TaskLifecycleStage.EXECUTE,
            TaskLifecycleStage.VERIFY,
        ]

    def test_invalid_stage_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LifecycleMetadata(requested_stages=["specification"])  # type: ignore[list-item]

    def test_invalid_policy_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LifecycleMetadata(stage_policy="manual_gate_between_stages")  # type: ignore[arg-type]

    def test_result_can_carry_lifecycle_outcome(self) -> None:
        outcome = LifecycleOutcome(
            completed_stages=[TaskLifecycleStage.PLAN],
            failed_stages=[TaskLifecycleStage.EXECUTE],
        )
        result = ExecutionResult(
            run_id="r",
            proposal_id="p",
            decision_id="d",
            status=ExecutionStatus.FAILED,
            success=False,
            lifecycle_outcome=outcome,
        )
        assert result.lifecycle_outcome is not None
        assert result.lifecycle_outcome.completed_stages == [TaskLifecycleStage.PLAN]
        assert result.lifecycle_outcome.failed_stages == [TaskLifecycleStage.EXECUTE]


# ---------------------------------------------------------------------------
# Runner — happy path
# ---------------------------------------------------------------------------


class TestRunnerHappyPath:
    def _runner(self) -> LifecycleRunner:
        return LifecycleRunner(
            StageHandlers(plan=_ok_plan, execute=_ok_execute, verify=_all_pass_verify)
        )

    def test_full_run_all_stages_complete(self) -> None:
        req = _make_request(LifecycleMetadata())
        result = self._runner().run(request=req, metadata=req.lifecycle)
        assert result.outcome.completed_stages == [
            TaskLifecycleStage.PLAN,
            TaskLifecycleStage.EXECUTE,
            TaskLifecycleStage.VERIFY,
        ]
        assert result.outcome.failed_stages == []
        assert result.outcome.skipped_stages == []
        assert result.verify is not None
        assert result.verify.verification_status == "pass"
        assert result.verify.failures == []

    def test_partial_subset_runs_only_requested_stages(self) -> None:
        meta = LifecycleMetadata(
            requested_stages=[TaskLifecycleStage.PLAN, TaskLifecycleStage.EXECUTE]
        )
        req = _make_request(meta)
        result = self._runner().run(request=req, metadata=meta)
        assert result.outcome.completed_stages == [
            TaskLifecycleStage.PLAN,
            TaskLifecycleStage.EXECUTE,
        ]
        assert result.verify is None

    def test_plan_emitted_checks_consumed_by_verify(self) -> None:
        req = _make_request(LifecycleMetadata())
        result = self._runner().run(request=req, metadata=req.lifecycle)
        assert result.plan is not None
        assert {c.check_id for c in result.plan.checks} == {
            "tests_pass",
            "diff_under_limit",
        }
        assert result.verify is not None
        assert {c.check_id for c in result.verify.checks} == {
            "tests_pass",
            "diff_under_limit",
        }


# ---------------------------------------------------------------------------
# Runner — failure handling
# ---------------------------------------------------------------------------


class TestRunnerFailures:
    def test_stop_on_first_failure_skips_later_stages(self) -> None:
        runner = LifecycleRunner(
            StageHandlers(plan=_ok_plan, execute=_exec_raises, verify=_all_pass_verify)
        )
        req = _make_request(LifecycleMetadata())
        result = runner.run(request=req, metadata=req.lifecycle)
        assert TaskLifecycleStage.EXECUTE in result.outcome.failed_stages
        assert TaskLifecycleStage.VERIFY in result.outcome.skipped_stages
        assert TaskLifecycleStage.VERIFY not in result.outcome.completed_stages

    def test_run_all_best_effort_continues_after_failure(self) -> None:
        # plan succeeds → execute raises → verify still runs (and will
        # itself fail because verify needs execute_out which is None).
        runner = LifecycleRunner(
            StageHandlers(plan=_ok_plan, execute=_exec_raises, verify=_all_pass_verify)
        )
        meta = LifecycleMetadata(
            stage_policy=LifecycleStagePolicy.RUN_ALL_BEST_EFFORT
        )
        req = _make_request(meta)
        result = runner.run(request=req, metadata=meta)
        assert TaskLifecycleStage.EXECUTE in result.outcome.failed_stages
        # Under best-effort, verify is attempted (and fails on the missing
        # execute output) — it should NOT land in skipped.
        assert TaskLifecycleStage.VERIFY not in result.outcome.skipped_stages

    def test_verify_failure_stops_under_strict_policy(self) -> None:
        runner = LifecycleRunner(
            StageHandlers(plan=_ok_plan, execute=_ok_execute, verify=_failing_verify)
        )
        req = _make_request(LifecycleMetadata())
        result = runner.run(request=req, metadata=req.lifecycle)
        assert TaskLifecycleStage.VERIFY in result.outcome.failed_stages
        assert result.verify is not None
        assert result.verify.verification_status == "fail"
        assert len(result.verify.failures) == 1
        assert result.verify.failures[0].check_id == "diff_under_limit"

    def test_verify_handler_missing_check_id_implicitly_fails(self) -> None:
        """If verify omits a plan-declared check, runner records it as
        failed. Plan declares; verify must report on every check."""

        def _partial_verify(*, request, plan, execution):  # type: ignore[no-untyped-def]
            return [CheckResult(check_id="tests_pass", passed=True)]
            # diff_under_limit intentionally absent

        runner = LifecycleRunner(
            StageHandlers(plan=_ok_plan, execute=_ok_execute, verify=_partial_verify)
        )
        req = _make_request(LifecycleMetadata())
        result = runner.run(request=req, metadata=req.lifecycle)
        assert result.verify is not None
        missing = next(c for c in result.verify.checks if c.check_id == "diff_under_limit")
        assert missing.passed is False
        assert "not reported" in (missing.reason or "")


# ---------------------------------------------------------------------------
# StageReport surfaces
# ---------------------------------------------------------------------------


class TestStageReports:
    def test_reports_emitted_per_stage(self) -> None:
        runner = LifecycleRunner(
            StageHandlers(plan=_ok_plan, execute=_ok_execute, verify=_all_pass_verify)
        )
        req = _make_request(LifecycleMetadata())
        result = runner.run(request=req, metadata=req.lifecycle)
        statuses = {(r.stage, r.status) for r in result.outcome.reports}
        assert (TaskLifecycleStage.PLAN, StageStatus.SUCCEEDED) in statuses
        assert (TaskLifecycleStage.EXECUTE, StageStatus.SUCCEEDED) in statuses
        assert (TaskLifecycleStage.VERIFY, StageStatus.SUCCEEDED) in statuses
