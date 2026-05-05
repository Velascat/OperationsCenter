# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Contract / schema alignment tests for recovery-loop additions.

Execution contracts are Pydantic-only (no separate JSON schema files), so
alignment means: Pydantic model accepts the new optional fields without
breaking old payloads.
"""

from __future__ import annotations

from pathlib import Path

from operations_center.contracts.enums import ExecutionStatus
from operations_center.contracts.execution import (
    ExecutionRequest,
    ExecutionResult,
    RecoveryActionSummary,
    RecoveryMetadataSummary,
)


class TestExecutionRequestIdempotent:
    def test_default_idempotent_is_false(self):
        req = ExecutionRequest(
            proposal_id="p", decision_id="d",
            goal_text="g", repo_key="r",
            clone_url="https://x/y.git", base_branch="main",
            task_branch="auto/x", workspace_path=Path("/tmp/ws"),
        )
        assert req.idempotent is False

    def test_idempotent_can_be_set(self):
        req = ExecutionRequest(
            proposal_id="p", decision_id="d",
            goal_text="g", repo_key="r",
            clone_url="https://x/y.git", base_branch="main",
            task_branch="auto/x", workspace_path=Path("/tmp/ws"),
            idempotent=True,
        )
        assert req.idempotent is True


class TestExecutionResultRecoveryField:
    def test_default_recovery_is_none(self):
        res = ExecutionResult(
            run_id="r1", proposal_id="p", decision_id="d",
            status=ExecutionStatus.SUCCEEDED, success=True,
        )
        assert res.recovery is None

    def test_legacy_payload_without_recovery_validates(self):
        # Simulate an "old" payload from before recovery field was added.
        legacy_dict = {
            "run_id": "r1",
            "proposal_id": "p",
            "decision_id": "d",
            "status": "succeeded",
            "success": True,
            "validation": {"status": "skipped"},
        }
        res = ExecutionResult.model_validate(legacy_dict)
        assert res.recovery is None

    def test_recovery_summary_round_trips_through_json(self):
        meta = RecoveryMetadataSummary(
            attempts=2,
            actions=[
                RecoveryActionSummary(
                    attempt=1,
                    failure_kind="timeout",
                    decision="retry_same_request",
                    reason="retryable",
                    handler_name="retry_same_request",
                ),
                RecoveryActionSummary(
                    attempt=2,
                    failure_kind="none",
                    decision="accept",
                    reason="result.success",
                ),
            ],
            final_decision="accept",
        )
        res = ExecutionResult(
            run_id="r1", proposal_id="p", decision_id="d",
            status=ExecutionStatus.SUCCEEDED, success=True,
            recovery=meta,
        )
        as_json = res.model_dump_json()
        re_validated = ExecutionResult.model_validate_json(as_json)
        assert re_validated.recovery == meta
        assert re_validated.recovery.attempts == 2
        assert len(re_validated.recovery.actions) == 2

    def test_result_remains_frozen(self):
        import pytest
        res = ExecutionResult(
            run_id="r1", proposal_id="p", decision_id="d",
            status=ExecutionStatus.SUCCEEDED, success=True,
        )
        with pytest.raises(Exception):  # noqa: BLE001 — Pydantic raises ValidationError on frozen
            res.run_id = "r2"


class TestRecoveryActionSummary:
    def test_modified_fields_default_empty(self):
        a = RecoveryActionSummary(
            attempt=1, failure_kind="timeout",
            decision="retry_same_request", reason="x",
        )
        assert a.modified_fields == []

    def test_delay_seconds_optional(self):
        a = RecoveryActionSummary(
            attempt=1, failure_kind="rate_limit",
            decision="retry_same_request", reason="x",
            delay_seconds=2.5,
        )
        assert a.delay_seconds == 2.5
