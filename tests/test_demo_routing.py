# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for stub routing and demo policy gate."""

from __future__ import annotations

from pathlib import Path


from operations_center.backends.demo_stub import DemoStubBackendAdapter
from operations_center.backends.factory import CanonicalBackendRegistry
from operations_center.contracts.enums import BackendName, ExecutionStatus, LaneName
from operations_center.contracts.routing import LaneDecision
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.execution.handoff import ExecutionRuntimeContext
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.policy.defaults import DEFAULT_REPO_POLICY
from operations_center.policy.engine import PolicyEngine
from operations_center.policy.models import (
    BranchGuardrail,
    PathPolicy,
    PolicyConfig,
    PolicyStatus,
    RepoPolicy,
    ReviewRequirement,
    ToolGuardrail,
)


def _demo_context(repo_key: str = "demo", **kw) -> PlanningContext:
    defaults = dict(
        goal_text="Write a tiny hello-world artifact",
        task_type="simple_edit",
        repo_key=repo_key,
        clone_url="demo://local",
        base_branch="main",
        risk_level="low",
        push_on_success=False,
        open_pr=False,
    )
    defaults.update(kw)
    return PlanningContext(**defaults)


def _demo_lane_decision(proposal_id: str) -> LaneDecision:
    return LaneDecision(
        proposal_id=proposal_id,
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=BackendName.DEMO_STUB,
        confidence=1.0,
        policy_rule_matched="demo.stub_routing",
        rationale="Offline stub routing — demo mode",
    )


def _permissive_engine(repo_key: str = "demo") -> PolicyEngine:
    demo_policy = RepoPolicy(
        repo_key=repo_key,
        enabled=True,
        path_policy=PathPolicy(rules=[], default_mode="allow"),
        branch_guardrail=BranchGuardrail(allow_direct_commit=True, require_pr=False),
        tool_guardrail=ToolGuardrail(network_mode="allowed"),
        validation_requirements=[],
        review_requirement=ReviewRequirement(autonomous_allowed=True),
    )
    config = PolicyConfig(repo_policies=[demo_policy], default_policy=DEFAULT_REPO_POLICY)
    return PolicyEngine.from_config(config)


def _blocking_engine(repo_key: str = "demo") -> PolicyEngine:
    blocking_policy = RepoPolicy(
        repo_key=repo_key,
        enabled=True,
        review_requirement=ReviewRequirement(
            autonomous_allowed=False,
            blocked_without_human=True,
        ),
    )
    config = PolicyConfig(repo_policies=[blocking_policy], default_policy=DEFAULT_REPO_POLICY)
    return PolicyEngine.from_config(config)


def _registry() -> CanonicalBackendRegistry:
    return CanonicalBackendRegistry({BackendName.DEMO_STUB: DemoStubBackendAdapter()})


class TestStubLaneDecision:
    def test_stub_decision_is_canonical_lane_decision(self) -> None:
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        assert isinstance(decision, LaneDecision)

    def test_stub_decision_references_proposal(self) -> None:
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        assert decision.proposal_id == proposal.proposal_id

    def test_stub_decision_selects_demo_backend(self) -> None:
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        assert decision.selected_backend == BackendName.DEMO_STUB

    def test_stub_decision_labeled_as_stub(self) -> None:
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        assert decision.policy_rule_matched == "demo.stub_routing"


class TestDemoPermissivePolicy:
    def test_demo_policy_allows_simple_edit(self) -> None:
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        engine = _permissive_engine()
        pd = engine.evaluate(proposal, decision)
        assert pd.status == PolicyStatus.ALLOW

    def test_policy_is_invoked_before_adapter(self, tmp_path: Path) -> None:
        """Confirm policy runs (ALLOW) before adapter; adapter produces artifact."""
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        bundle = ProposalDecisionBundle(proposal=proposal, decision=decision)
        runtime = ExecutionRuntimeContext(workspace_path=tmp_path, task_branch="demo/test")

        outcome = ExecutionCoordinator(
            adapter_registry=_registry(),
            policy_engine=_permissive_engine(),
        ).execute(bundle, runtime)

        assert outcome.policy_decision.status == PolicyStatus.ALLOW
        assert outcome.executed is True
        assert outcome.result.success is True


class TestDemoBlockedPolicy:
    def test_blocking_policy_prevents_execution(self, tmp_path: Path) -> None:
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        bundle = ProposalDecisionBundle(proposal=proposal, decision=decision)
        runtime = ExecutionRuntimeContext(workspace_path=tmp_path, task_branch="demo/test")

        outcome = ExecutionCoordinator(
            adapter_registry=_registry(),
            policy_engine=_blocking_engine(),
        ).execute(bundle, runtime)

        assert outcome.executed is False
        assert outcome.result.status in {ExecutionStatus.SKIPPED, ExecutionStatus.FAILED}

    def test_blocking_policy_does_not_write_artifact(self, tmp_path: Path) -> None:
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        bundle = ProposalDecisionBundle(proposal=proposal, decision=decision)
        runtime = ExecutionRuntimeContext(workspace_path=tmp_path, task_branch="demo/test")

        ExecutionCoordinator(
            adapter_registry=_registry(),
            policy_engine=_blocking_engine(),
        ).execute(bundle, runtime)

        artifact_path = tmp_path / "artifacts" / "demo_result.txt"
        assert not artifact_path.exists(), "adapter must not write artifact when policy blocks"

    def test_blocked_outcome_has_record_and_trace(self, tmp_path: Path) -> None:
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        bundle = ProposalDecisionBundle(proposal=proposal, decision=decision)
        runtime = ExecutionRuntimeContext(workspace_path=tmp_path, task_branch="demo/test")

        outcome = ExecutionCoordinator(
            adapter_registry=_registry(),
            policy_engine=_blocking_engine(),
        ).execute(bundle, runtime)

        assert outcome.record is not None
        assert outcome.trace is not None


class TestFullDemoBoundary:
    def test_coordinator_returns_all_required_fields(self, tmp_path: Path) -> None:
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        bundle = ProposalDecisionBundle(proposal=proposal, decision=decision)
        runtime = ExecutionRuntimeContext(workspace_path=tmp_path, task_branch="demo/test")

        outcome = ExecutionCoordinator(
            adapter_registry=_registry(),
            policy_engine=_permissive_engine(),
        ).execute(bundle, runtime)

        assert outcome.request is not None
        assert outcome.policy_decision is not None
        assert outcome.result is not None
        assert outcome.record is not None
        assert outcome.trace is not None
        assert outcome.executed is True

    def test_request_ids_chain_through_to_result(self, tmp_path: Path) -> None:
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        bundle = ProposalDecisionBundle(proposal=proposal, decision=decision)
        runtime = ExecutionRuntimeContext(workspace_path=tmp_path, task_branch="demo/test")

        outcome = ExecutionCoordinator(
            adapter_registry=_registry(),
            policy_engine=_permissive_engine(),
        ).execute(bundle, runtime)

        assert outcome.request.proposal_id == proposal.proposal_id
        assert outcome.request.decision_id == decision.decision_id
        assert outcome.result.proposal_id == proposal.proposal_id
        assert outcome.result.decision_id == decision.decision_id
        assert outcome.record.run_id == outcome.result.run_id

    def test_trace_headline_reflects_result(self, tmp_path: Path) -> None:
        proposal = build_proposal(_demo_context())
        decision = _demo_lane_decision(proposal.proposal_id)
        bundle = ProposalDecisionBundle(proposal=proposal, decision=decision)
        runtime = ExecutionRuntimeContext(workspace_path=tmp_path, task_branch="demo/test")

        outcome = ExecutionCoordinator(
            adapter_registry=_registry(),
            policy_engine=_permissive_engine(),
        ).execute(bundle, runtime)

        assert "SUCCEEDED" in outcome.trace.headline
        assert "demo_stub" in outcome.trace.headline
