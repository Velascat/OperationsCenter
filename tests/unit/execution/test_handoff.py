# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for canonical execution handoff construction."""

from __future__ import annotations

from pathlib import Path

from operations_center.contracts.enums import BackendName, LaneName
from operations_center.contracts.routing import LaneDecision
from operations_center.execution.handoff import ExecutionRequestBuilder, ExecutionRuntimeContext
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.policy.models import PolicyDecision, PolicyStatus


def _bundle() -> ProposalDecisionBundle:
    proposal = build_proposal(
        PlanningContext(
            goal_text="Fix lint failures",
            task_type="lint_fix",
            repo_key="svc",
            clone_url="https://example.invalid/svc.git",
            base_branch="main",
            allowed_paths=["src/**"],
            validation_commands=["pytest -q"],
            max_changed_files=3,
            timeout_seconds=120,
        )
    )
    return ProposalDecisionBundle(
        proposal=proposal,
        decision=LaneDecision(
            proposal_id=proposal.proposal_id,
            selected_lane=LaneName.AIDER_LOCAL,
            selected_backend=BackendName.DIRECT_LOCAL,
        ),
    )


def test_builder_constructs_canonical_execution_request() -> None:
    bundle = _bundle()
    runtime = ExecutionRuntimeContext(
        workspace_path=Path("/tmp/workspace"),
        task_branch="auto/lint-fix",
    )

    request = ExecutionRequestBuilder().build(bundle, runtime)

    assert request.proposal_id == bundle.proposal.proposal_id
    assert request.decision_id == bundle.decision.decision_id
    assert request.repo_key == "svc"
    assert request.base_branch == "main"
    assert request.task_branch == "auto/lint-fix"
    assert request.allowed_paths == ["src/**"]
    assert request.validation_commands == ["pytest -q"]
    assert request.max_changed_files == 3


def test_builder_uses_policy_effective_scope_when_present() -> None:
    bundle = _bundle()
    runtime = ExecutionRuntimeContext(
        workspace_path=Path("/tmp/workspace"),
        task_branch="auto/lint-fix",
    )
    policy = PolicyDecision(
        status=PolicyStatus.ALLOW,
        effective_scope=["docs/**"],
    )

    request = ExecutionRequestBuilder().build(bundle, runtime, policy)

    assert request.allowed_paths == ["docs/**"]
