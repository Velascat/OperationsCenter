# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for planning/proposal_builder.py."""

from __future__ import annotations

import pytest

from operations_center.contracts.enums import (
    ExecutionMode,
    Priority,
    RiskLevel,
    TaskType,
)
from operations_center.planning.models import PlanningContext
from operations_center.planning.proposal_builder import (
    build_proposal,
    build_proposal_with_result,
)


def _ctx(**kw) -> PlanningContext:
    defaults = dict(
        goal_text="Fix all lint errors in src/",
        task_type="lint_fix",
        repo_key="api-service",
        clone_url="https://github.com/org/api-service.git",
    )
    defaults.update(kw)
    return PlanningContext(**defaults)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_missing_goal_text_raises():
    with pytest.raises(ValueError, match="goal_text"):
        build_proposal(_ctx(goal_text="   "))


def test_missing_repo_key_raises():
    with pytest.raises(ValueError, match="repo_key"):
        build_proposal(_ctx(repo_key=""))


def test_missing_clone_url_raises():
    with pytest.raises(ValueError, match="clone_url"):
        build_proposal(_ctx(clone_url=""))


def test_multiple_errors_combined():
    with pytest.raises(ValueError) as exc_info:
        build_proposal(_ctx(goal_text="", repo_key="", clone_url=""))
    msg = str(exc_info.value)
    assert "goal_text" in msg
    assert "repo_key" in msg
    assert "clone_url" in msg


# ---------------------------------------------------------------------------
# Enum mapping — valid values
# ---------------------------------------------------------------------------


def test_task_type_mapped():
    p = build_proposal(_ctx(task_type="lint_fix"))
    assert p.task_type == TaskType.LINT_FIX


def test_execution_mode_mapped():
    p = build_proposal(_ctx(execution_mode="goal"))
    assert p.execution_mode == ExecutionMode.GOAL


def test_risk_level_mapped():
    p = build_proposal(_ctx(risk_level="medium"))
    assert p.risk_level == RiskLevel.MEDIUM


def test_priority_mapped():
    p = build_proposal(_ctx(priority="high"))
    assert p.priority == Priority.HIGH


# ---------------------------------------------------------------------------
# Enum mapping — unknown values fall back gracefully
# ---------------------------------------------------------------------------


def test_unknown_task_type_falls_back():
    p = build_proposal(_ctx(task_type="not_a_real_type"))
    assert p.task_type == TaskType.UNKNOWN


def test_unknown_execution_mode_falls_back():
    p = build_proposal(_ctx(execution_mode="not_a_mode"))
    assert p.execution_mode == ExecutionMode.GOAL


def test_unknown_risk_level_falls_back():
    p = build_proposal(_ctx(risk_level="extreme"))
    assert p.risk_level == RiskLevel.LOW


def test_unknown_priority_falls_back():
    p = build_proposal(_ctx(priority="urgent"))
    assert p.priority == Priority.NORMAL


# ---------------------------------------------------------------------------
# TaskTarget
# ---------------------------------------------------------------------------


def test_target_fields():
    p = build_proposal(_ctx(
        repo_key="svc",
        clone_url="https://git.example.com/svc.git",
        base_branch="develop",
        allowed_paths=["src/**", "tests/**"],
    ))
    assert p.target.repo_key == "svc"
    assert p.target.clone_url == "https://git.example.com/svc.git"
    assert p.target.base_branch == "develop"
    assert p.target.allowed_paths == ["src/**", "tests/**"]


def test_allowed_paths_default_empty():
    p = build_proposal(_ctx())
    assert p.target.allowed_paths == []


# ---------------------------------------------------------------------------
# ExecutionConstraints
# ---------------------------------------------------------------------------


def test_constraints_fields():
    p = build_proposal(_ctx(
        max_changed_files=10,
        timeout_seconds=600,
        require_clean_validation=False,
        allowed_paths=["src/**"],
    ))
    assert p.constraints.max_changed_files == 10
    assert p.constraints.timeout_seconds == 600
    assert p.constraints.require_clean_validation is False
    assert p.constraints.allowed_paths == ["src/**"]


# ---------------------------------------------------------------------------
# ValidationProfile
# ---------------------------------------------------------------------------


def test_validation_profile_fields():
    p = build_proposal(_ctx(
        validation_profile_name="strict",
        validation_commands=["ruff check src/", "mypy src/"],
    ))
    assert p.validation_profile.profile_name == "strict"
    assert p.validation_profile.commands == ["ruff check src/", "mypy src/"]


# ---------------------------------------------------------------------------
# BranchPolicy
# ---------------------------------------------------------------------------


def test_branch_policy_fields():
    p = build_proposal(_ctx(push_on_success=False, open_pr=True))
    assert p.branch_policy.push_on_success is False
    assert p.branch_policy.open_pr is True


# ---------------------------------------------------------------------------
# task_id derivation
# ---------------------------------------------------------------------------


def test_explicit_task_id_preserved():
    p = build_proposal(_ctx(task_id="TASK-99"))
    assert p.task_id == "TASK-99"


def test_auto_task_id_derived_when_empty():
    p = build_proposal(_ctx(task_id=""))
    assert p.task_id.startswith("auto-")


def test_auto_task_id_is_stable():
    p1 = build_proposal(_ctx(task_id=""))
    p2 = build_proposal(_ctx(task_id=""))
    assert p1.task_id == p2.task_id


def test_auto_task_id_changes_with_goal():
    p1 = build_proposal(_ctx(task_id="", goal_text="Fix lint errors"))
    p2 = build_proposal(_ctx(task_id="", goal_text="Refactor auth module"))
    assert p1.task_id != p2.task_id


# ---------------------------------------------------------------------------
# Labels and proposer
# ---------------------------------------------------------------------------


def test_labels_preserved():
    p = build_proposal(_ctx(labels=["local_only", "fast"]))
    assert "local_only" in p.labels
    assert "fast" in p.labels


def test_proposer_preserved():
    p = build_proposal(_ctx(proposer="ci-pipeline"))
    assert p.proposer == "ci-pipeline"


# ---------------------------------------------------------------------------
# ProposalBuildResult
# ---------------------------------------------------------------------------


def test_build_proposal_with_result_wraps_proposal():
    ctx = _ctx()
    result = build_proposal_with_result(ctx)
    assert result.proposal is not None
    assert result.context is ctx
    assert result.proposal.goal_text == ctx.goal_text


def test_build_proposal_with_result_has_timestamp():
    result = build_proposal_with_result(_ctx())
    assert result.built_at is not None


# ---------------------------------------------------------------------------
# proposal_id is auto-generated UUID
# ---------------------------------------------------------------------------


def test_proposal_id_is_unique():
    p1 = build_proposal(_ctx())
    p2 = build_proposal(_ctx())
    assert p1.proposal_id != p2.proposal_id


def test_proposal_id_is_nonempty():
    p = build_proposal(_ctx())
    assert p.proposal_id
