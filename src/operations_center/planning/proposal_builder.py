# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
planning/proposal_builder.py — maps PlanningContext → canonical TaskProposal.

The proposal builder is a pure function. It does not call SwitchBoard, invoke
backends, or access external state. It translates OperationsCenter context into
a canonical TaskProposal that remains backend-agnostic.

Preferred lane/backend hints (if provided via labels) are preserved as labels
on the proposal — they remain non-authoritative hints that SwitchBoard may or
may not respect. SwitchBoard owns the final routing decision.
"""

from __future__ import annotations

from operations_center.contracts.common import (
    BranchPolicy,
    ExecutionConstraints,
    TaskTarget,
    ValidationProfile,
)
from operations_center.contracts.enums import (
    ExecutionMode,
    Priority,
    RiskLevel,
    TaskType,
)
from operations_center.contracts.proposal import TaskProposal

from .models import PlanningContext, ProposalBuildResult


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_proposal(context: PlanningContext) -> TaskProposal:
    """Map a PlanningContext into a canonical TaskProposal.

    This function is the only place where OperationsCenter internal context is
    translated into canonical contract types. All canonical field values come
    from the context; none come from backend-specific knowledge.

    Raises:
        ValueError: if required context fields are missing or invalid.
    """
    _validate(context)

    task_type = _task_type(context.task_type)
    execution_mode = _execution_mode(context.execution_mode)
    risk_level = _risk_level(context.risk_level)
    priority = _priority(context.priority)

    target = TaskTarget(
        repo_key=context.repo_key,
        clone_url=context.clone_url,
        base_branch=context.base_branch,
        allowed_paths=list(context.allowed_paths),
    )

    constraints = ExecutionConstraints(
        max_changed_files=context.max_changed_files,
        timeout_seconds=context.timeout_seconds,
        allowed_paths=list(context.allowed_paths),
        require_clean_validation=context.require_clean_validation,
    )

    validation_profile = ValidationProfile(
        profile_name=context.validation_profile_name,
        commands=list(context.validation_commands),
    )

    branch_policy = BranchPolicy(
        push_on_success=context.push_on_success,
        open_pr=context.open_pr,
    )

    return TaskProposal(
        task_id=context.task_id or _derive_task_id(context),
        project_id=context.project_id,
        task_type=task_type,
        execution_mode=execution_mode,
        goal_text=context.goal_text,
        constraints_text=context.constraints_text,
        target=target,
        priority=priority,
        risk_level=risk_level,
        constraints=constraints,
        validation_profile=validation_profile,
        branch_policy=branch_policy,
        proposer=context.proposer,
        labels=list(context.labels),
    )


def build_proposal_with_result(context: PlanningContext) -> ProposalBuildResult:
    """Build a TaskProposal and return it wrapped in a ProposalBuildResult."""
    proposal = build_proposal(context)
    return ProposalBuildResult(proposal=proposal, context=context)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(context: PlanningContext) -> None:
    errors: list[str] = []
    if not context.goal_text.strip():
        errors.append("goal_text must not be empty")
    if not context.repo_key:
        errors.append("repo_key must not be empty")
    if not context.clone_url:
        errors.append("clone_url must not be empty")
    if errors:
        raise ValueError(f"PlanningContext validation failed: {'; '.join(errors)}")


# ---------------------------------------------------------------------------
# Enum mapping
# ---------------------------------------------------------------------------

def _task_type(value: str) -> TaskType:
    try:
        return TaskType(value)
    except ValueError:
        return TaskType.UNKNOWN


def _execution_mode(value: str) -> ExecutionMode:
    try:
        return ExecutionMode(value)
    except ValueError:
        return ExecutionMode.GOAL


def _risk_level(value: str) -> RiskLevel:
    try:
        return RiskLevel(value)
    except ValueError:
        return RiskLevel.LOW


def _priority(value: str) -> Priority:
    try:
        return Priority(value)
    except ValueError:
        return Priority.NORMAL


def _derive_task_id(context: PlanningContext) -> str:
    """Derive a stable task ID when none is provided."""
    slug = context.task_type.replace("_", "-")[:20]
    import hashlib
    h = hashlib.sha1(context.goal_text.encode(), usedforsecurity=False).hexdigest()[:8]
    return f"auto-{slug}-{h}"
