# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
Shared fixtures for policy unit tests.

Provides factory functions for building canonical contract objects
(TaskProposal, LaneDecision, ExecutionRequest) with sensible defaults
so test bodies stay focused on the policy dimension being tested.
"""

from __future__ import annotations


from operations_center.contracts.common import BranchPolicy, TaskTarget, ValidationProfile
from operations_center.contracts.enums import (
    BackendName,
    ExecutionMode,
    LaneName,
    RiskLevel,
    TaskType,
)
from operations_center.contracts.proposal import TaskProposal
from operations_center.contracts.routing import LaneDecision
from operations_center.policy.models import (
    BranchGuardrail,
    PathPolicy,
    PathScopeRule,
    PolicyConfig,
    RepoPolicy,
    ReviewRequirement,
    ToolGuardrail,
    ValidationRequirement,
)


# ---------------------------------------------------------------------------
# Proposal factory
# ---------------------------------------------------------------------------


def make_proposal(
    *,
    repo_key: str = "test-repo",
    base_branch: str = "main",
    task_type: TaskType = TaskType.BUG_FIX,
    risk_level: RiskLevel = RiskLevel.LOW,
    allowed_paths: list[str] | None = None,
    labels: list[str] | None = None,
    branch_prefix: str = "auto/",
    open_pr: bool = False,
    validation_commands: list[str] | None = None,
    goal_text: str = "Fix the bug",
) -> TaskProposal:
    target = TaskTarget(
        repo_key=repo_key,
        clone_url=f"git@github.com:org/{repo_key}.git",
        base_branch=base_branch,
        allowed_paths=allowed_paths or [],
    )
    branch_policy = BranchPolicy(
        branch_prefix=branch_prefix,
        push_on_success=True,
        open_pr=open_pr,
    )
    validation_profile = ValidationProfile(
        profile_name="default",
        commands=validation_commands or [],
    )
    return TaskProposal(
        task_id="TASK-TEST",
        project_id="proj-test",
        task_type=task_type,
        execution_mode=ExecutionMode.GOAL,
        goal_text=goal_text,
        target=target,
        risk_level=risk_level,
        branch_policy=branch_policy,
        validation_profile=validation_profile,
        labels=labels or [],
    )


# ---------------------------------------------------------------------------
# Lane decision factory
# ---------------------------------------------------------------------------


def make_decision(
    *,
    lane: LaneName = LaneName.CLAUDE_CLI,
    backend: BackendName = BackendName.OPENCLAW,
    proposal_id: str = "proposal-test",
) -> LaneDecision:
    return LaneDecision(
        proposal_id=proposal_id,
        selected_lane=lane,
        selected_backend=backend,
    )


def local_decision(proposal_id: str = "proposal-test") -> LaneDecision:
    return make_decision(
        lane=LaneName.AIDER_LOCAL,
        backend=BackendName.KODO,
        proposal_id=proposal_id,
    )


def remote_decision(proposal_id: str = "proposal-test") -> LaneDecision:
    return make_decision(
        lane=LaneName.CLAUDE_CLI,
        backend=BackendName.OPENCLAW,
        proposal_id=proposal_id,
    )


# ---------------------------------------------------------------------------
# Policy config helpers
# ---------------------------------------------------------------------------


def make_repo_policy(
    *,
    repo_key: str = "test-repo",
    enabled: bool = True,
    risk_profile: str = "standard",
    path_rules: list[PathScopeRule] | None = None,
    path_default_mode: str = "allow",
    allow_direct_commit: bool = False,
    require_branch: bool = True,
    require_pr: bool = False,
    allowed_base_branches: list[str] | None = None,
    network_mode: str = "allowed",
    allow_destructive_actions: bool = False,
    blocked_tool_classes: list[str] | None = None,
    validation_requirements: list[ValidationRequirement] | None = None,
    autonomous_allowed: bool = True,
    require_review_for_risk_levels: list[str] | None = None,
    require_review_for_task_types: list[str] | None = None,
    blocked_without_human: bool = False,
    allowed_task_types: list[str] | None = None,
    blocked_task_types: list[str] | None = None,
) -> RepoPolicy:
    return RepoPolicy(
        repo_key=repo_key,
        enabled=enabled,
        risk_profile=risk_profile,
        path_policy=PathPolicy(
            rules=path_rules or [],
            default_mode=path_default_mode,
        ),
        branch_guardrail=BranchGuardrail(
            allow_direct_commit=allow_direct_commit,
            require_branch=require_branch,
            require_pr=require_pr,
            allowed_base_branches=allowed_base_branches or [],
        ),
        tool_guardrail=ToolGuardrail(
            network_mode=network_mode,
            allow_destructive_actions=allow_destructive_actions,
            blocked_tool_classes=blocked_tool_classes or [],
        ),
        validation_requirements=validation_requirements or [],
        review_requirement=ReviewRequirement(
            autonomous_allowed=autonomous_allowed,
            require_review_for_risk_levels=require_review_for_risk_levels or [],
            require_review_for_task_types=require_review_for_task_types or [],
            blocked_without_human=blocked_without_human,
        ),
        allowed_task_types=allowed_task_types or [],
        blocked_task_types=blocked_task_types or [],
    )


def make_policy_config(policy: RepoPolicy) -> PolicyConfig:
    return PolicyConfig(
        repo_policies=[policy],
        default_policy=policy,
    )


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------
