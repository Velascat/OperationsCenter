# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the runtime-binding policy (Option B)."""

from __future__ import annotations

import textwrap

import pytest
from cxrp.contracts.runtime_binding import RuntimeBinding
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode

from operations_center.contracts import LaneDecision, TaskProposal
from operations_center.contracts.common import TaskTarget
from operations_center.contracts.enums import (
    BackendName,
    ExecutionMode,
    LaneName,
    Priority,
    RiskLevel,
    TaskType,
)
from operations_center.policy.runtime_binding_policy import (
    DEFAULT_POLICY,
    RuntimeBindingPolicy,
    RuntimeBindingRule,
)


def _proposal(task_type: TaskType = TaskType.LINT_FIX) -> TaskProposal:
    return TaskProposal(
        task_id="TASK-1",
        project_id="proj-1",
        task_type=task_type,
        execution_mode=ExecutionMode.GOAL,
        goal_text="do thing",
        target=TaskTarget(
            repo_key="svc",
            clone_url="https://git.example.com/svc.git",
            base_branch="main",
        ),
        risk_level=RiskLevel.LOW,
        priority=Priority.NORMAL,
        labels=[],
    )


def _decision(lane: LaneName = LaneName.CLAUDE_CLI) -> LaneDecision:
    return LaneDecision(
        proposal_id="TASK-1",
        selected_lane=lane,
        selected_backend=BackendName.KODO,
        confidence=0.9,
        rationale="test",
    )


class TestRuleMatching:
    def test_empty_when_matches_anything(self):
        rule = RuntimeBindingRule(
            name="any",
            when={},
            kind="cli_subscription",
            provider="anthropic",
            model="sonnet",
        )
        assert rule.matches({"task_type": "refactor", "lane": "claude_cli"})
        assert rule.matches({})

    def test_partial_when_must_all_match(self):
        rule = RuntimeBindingRule(
            name="strict",
            when={"task_type": "refactor", "lane": "claude_cli"},
            kind="cli_subscription",
        )
        assert rule.matches({"task_type": "refactor", "lane": "claude_cli"})
        assert not rule.matches({"task_type": "refactor", "lane": "aider_local"})
        assert not rule.matches({"task_type": "lint_fix", "lane": "claude_cli"})

    def test_to_binding_produces_canonical_cxrp_type(self):
        rule = RuntimeBindingRule(
            name="r",
            when={},
            kind="cli_subscription",
            provider="anthropic",
            model="opus",
        )
        binding = rule.to_binding()
        assert isinstance(binding, RuntimeBinding)
        assert binding.kind == RuntimeKind.CLI_SUBSCRIPTION
        assert binding.selection_mode == SelectionMode.POLICY_SELECTED
        assert binding.model == "opus"
        assert binding.provider == "anthropic"


class TestPolicySelection:
    def test_first_matching_rule_wins(self):
        policy = RuntimeBindingPolicy(
            rules=(
                RuntimeBindingRule(
                    name="refactor_premium",
                    when={"task_type": "refactor"},
                    kind="cli_subscription",
                    provider="anthropic",
                    model="opus",
                ),
                RuntimeBindingRule(
                    name="catch_all_sonnet",
                    when={},
                    kind="cli_subscription",
                    provider="anthropic",
                    model="sonnet",
                ),
            ),
        )
        binding = policy.select(_proposal(TaskType.REFACTOR), _decision())
        assert binding.model == "opus"

    def test_falls_through_to_default(self):
        policy = RuntimeBindingPolicy(
            rules=(
                RuntimeBindingRule(
                    name="refactor_only",
                    when={"task_type": "refactor"},
                    kind="cli_subscription",
                    provider="anthropic",
                    model="opus",
                ),
            ),
            default=RuntimeBindingRule(
                name="default",
                when={},
                kind="cli_subscription",
                provider="anthropic",
                model="sonnet",
            ),
        )
        binding = policy.select(_proposal(TaskType.LINT_FIX), _decision())
        assert binding.model == "sonnet"

    def test_no_rule_no_default_returns_none(self):
        policy = RuntimeBindingPolicy(rules=())
        assert policy.select(_proposal(), _decision()) is None

    def test_no_rule_match_no_default_returns_none(self):
        policy = RuntimeBindingPolicy(
            rules=(
                RuntimeBindingRule(
                    name="refactor_only",
                    when={"task_type": "refactor"},
                    kind="cli_subscription",
                    provider="anthropic",
                    model="opus",
                ),
            ),
        )
        assert policy.select(_proposal(TaskType.LINT_FIX), _decision()) is None

    def test_match_uses_lane_attribute(self):
        policy = RuntimeBindingPolicy(
            rules=(
                RuntimeBindingRule(
                    name="claude_only",
                    when={"lane": "claude_cli"},
                    kind="cli_subscription",
                    provider="anthropic",
                    model="opus",
                ),
            ),
        )
        # claude_cli decision matches
        b = policy.select(_proposal(), _decision(LaneName.CLAUDE_CLI))
        assert b is not None and b.model == "opus"
        # aider_local decision does not
        assert policy.select(_proposal(), _decision(LaneName.AIDER_LOCAL)) is None


class TestYAMLLoading:
    def test_from_yaml_missing_file_returns_empty_policy(self, tmp_path):
        p = tmp_path / "nonexistent.yaml"
        policy = RuntimeBindingPolicy.from_yaml(p)
        assert policy.rules == ()
        assert policy.default is None

    def test_from_yaml_round_trip(self, tmp_path):
        p = tmp_path / "policy.yaml"
        p.write_text(textwrap.dedent("""\
            rules:
              - name: refactor_opus
                when:
                  task_type: refactor
                  lane: claude_cli
                bind:
                  kind: cli_subscription
                  provider: anthropic
                  model: opus
            default:
              bind:
                kind: cli_subscription
                provider: anthropic
                model: sonnet
        """), encoding="utf-8")
        policy = RuntimeBindingPolicy.from_yaml(p)
        assert len(policy.rules) == 1
        assert policy.rules[0].name == "refactor_opus"
        assert policy.rules[0].model == "opus"
        assert policy.default is not None
        assert policy.default.model == "sonnet"

    def test_from_yaml_invalid_kind_raises_at_load(self, tmp_path):
        """Invalid kind/selection_mode pair must fail at policy.select(), not at adapter time."""
        p = tmp_path / "policy.yaml"
        p.write_text(textwrap.dedent("""\
            rules:
              - name: bogus
                when: {}
                bind:
                  kind: not_a_real_kind
        """), encoding="utf-8")
        policy = RuntimeBindingPolicy.from_yaml(p)
        with pytest.raises(ValueError):
            policy.select(_proposal(), _decision())


class TestBundledDefaults:
    def test_default_policy_picks_opus_for_refactor_on_claude(self):
        b = DEFAULT_POLICY.select(_proposal(TaskType.REFACTOR), _decision(LaneName.CLAUDE_CLI))
        assert b is not None and b.model == "opus"

    def test_default_policy_picks_haiku_for_lint_on_claude(self):
        b = DEFAULT_POLICY.select(_proposal(TaskType.LINT_FIX), _decision(LaneName.CLAUDE_CLI))
        assert b is not None and b.model == "haiku"

    def test_default_policy_falls_through_to_sonnet(self):
        # An unmodelled (task_type, lane) pair should hit the default rule (sonnet).
        b = DEFAULT_POLICY.select(_proposal(TaskType.REFACTOR), _decision(LaneName.AIDER_LOCAL))
        assert b is not None and b.model == "sonnet"
