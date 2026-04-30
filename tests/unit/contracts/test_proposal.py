# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for TaskProposal."""

from __future__ import annotations

import json

import pytest

from operations_center.contracts.proposal import TaskProposal
from operations_center.contracts.common import TaskTarget, ExecutionConstraints, BranchPolicy, ValidationProfile
from operations_center.contracts.enums import (
    ExecutionMode,
    Priority,
    RiskLevel,
    TaskType,
)


def _target(**kw) -> TaskTarget:
    defaults = dict(repo_key="svc", clone_url="https://git.example.com/svc.git", base_branch="main")
    defaults.update(kw)
    return TaskTarget(**defaults)


def _minimal_proposal(**kw) -> TaskProposal:
    defaults = dict(
        task_id="TASK-1",
        project_id="proj-1",
        task_type=TaskType.LINT_FIX,
        execution_mode=ExecutionMode.GOAL,
        goal_text="Fix all ruff lint errors in src/",
        target=_target(),
    )
    defaults.update(kw)
    return TaskProposal(**defaults)


class TestTaskProposalConstruction:
    def test_minimal_required_fields(self):
        p = _minimal_proposal()
        assert p.task_id == "TASK-1"
        assert p.task_type == TaskType.LINT_FIX
        assert p.execution_mode == ExecutionMode.GOAL

    def test_auto_generated_proposal_id(self):
        p1 = _minimal_proposal()
        p2 = _minimal_proposal()
        assert p1.proposal_id != p2.proposal_id

    def test_defaults(self):
        p = _minimal_proposal()
        assert p.priority == Priority.NORMAL
        assert p.risk_level == RiskLevel.LOW
        assert p.constraints.timeout_seconds == 300
        assert p.branch_policy.push_on_success is True
        assert p.labels == []
        assert p.proposer is None
        assert p.constraints_text is None

    def test_with_all_optional_fields(self):
        p = TaskProposal(
            task_id="TASK-2",
            project_id="proj-2",
            task_type=TaskType.BUG_FIX,
            execution_mode=ExecutionMode.FIX_PR,
            goal_text="Fix the login redirect bug",
            constraints_text="Do not modify auth/tokens.py",
            target=_target(base_branch="develop"),
            priority=Priority.HIGH,
            risk_level=RiskLevel.MEDIUM,
            constraints=ExecutionConstraints(max_changed_files=10),
            validation_profile=ValidationProfile(profile_name="strict", commands=["pytest"]),
            branch_policy=BranchPolicy(open_pr=True),
            proposer="operations-center-v2",
            labels=["bug", "auth"],
        )
        assert p.priority == Priority.HIGH
        assert p.risk_level == RiskLevel.MEDIUM
        assert p.constraints.max_changed_files == 10
        assert p.branch_policy.open_pr is True
        assert "bug" in p.labels

    def test_is_frozen(self):
        p = _minimal_proposal()
        with pytest.raises(Exception):
            p.task_id = "OTHER"  # type: ignore[misc]


class TestTaskProposalSerialization:
    def test_json_round_trip(self):
        p = _minimal_proposal()
        restored = TaskProposal.model_validate_json(p.model_dump_json())
        assert restored == p

    def test_model_dump_contains_expected_keys(self):
        p = _minimal_proposal()
        d = p.model_dump()
        assert "proposal_id" in d
        assert "task_id" in d
        assert "goal_text" in d
        assert "target" in d
        assert "constraints" in d
        assert "branch_policy" in d
        assert "validation_profile" in d
        assert "proposed_at" in d

    def test_json_is_valid_json(self):
        p = _minimal_proposal()
        parsed = json.loads(p.model_dump_json())
        assert parsed["task_type"] == "lint_fix"
        assert parsed["priority"] == "normal"

    def test_dict_round_trip(self):
        p = _minimal_proposal()
        restored = TaskProposal.model_validate(p.model_dump())
        assert restored == p

    def test_nested_target_serialised(self):
        p = _minimal_proposal()
        d = p.model_dump()
        assert d["target"]["repo_key"] == "svc"
        assert d["target"]["base_branch"] == "main"


class TestTaskProposalValidation:
    def test_missing_task_id_raises(self):
        with pytest.raises(Exception):
            TaskProposal(
                project_id="p",
                task_type=TaskType.LINT_FIX,
                execution_mode=ExecutionMode.GOAL,
                goal_text="do thing",
                target=_target(),
            )

    def test_invalid_task_type_raises(self):
        with pytest.raises(Exception):
            TaskProposal(
                task_id="x",
                project_id="p",
                task_type="not_a_valid_type",
                execution_mode=ExecutionMode.GOAL,
                goal_text="do thing",
                target=_target(),
            )

    def test_empty_goal_text_accepted(self):
        # validation does not mandate non-empty goal text
        p = _minimal_proposal(goal_text="")
        assert p.goal_text == ""
