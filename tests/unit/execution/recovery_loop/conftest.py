# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Shared fixtures for recovery_loop tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import (
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult


def _make_request(*, idempotent: bool = False, **overrides) -> ExecutionRequest:
    base = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="do thing",
        repo_key="repo",
        clone_url="https://example.test/repo.git",
        base_branch="main",
        task_branch="task/run-1",
        workspace_path=Path("/tmp/ws"),
        idempotent=idempotent,
    )
    base.update(overrides)
    return ExecutionRequest(**base)


def _make_result(
    *,
    request: ExecutionRequest,
    success: bool = False,
    status: ExecutionStatus = ExecutionStatus.FAILED,
    failure_category: FailureReasonCategory | None = None,
    failure_reason: str | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=status,
        success=success,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=failure_category,
        failure_reason=failure_reason,
    )


@pytest.fixture
def make_request():
    return _make_request


@pytest.fixture
def make_result():
    return _make_result
