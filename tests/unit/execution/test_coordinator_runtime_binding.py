# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Coordinator integration tests for the new RuntimeBindingPolicy hook (Option B)."""

from __future__ import annotations

from pathlib import Path

from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import (
    BackendName,
    ExecutionStatus,
    LaneName,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionResult, RuntimeBindingSummary
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.execution.handoff import ExecutionRuntimeContext
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.policy.models import PolicyDecision, PolicyStatus
from operations_center.policy.runtime_binding_policy import (
    DEFAULT_POLICY,
    RuntimeBindingPolicy,
    RuntimeBindingRule,
)


# ---------------------------------------------------------------------------
# Reused stubs from test_coordinator.py shape
# ---------------------------------------------------------------------------


class _StubPolicyEngine:
    def __init__(self, decision: PolicyDecision) -> None:
        self._decision = decision

    def evaluate(self, proposal, decision, request=None) -> PolicyDecision:
        return self._decision


class _RecordingAdapter:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.last_request = None

    def execute(self, request):
        self.last_request = request
        return self.result


class _Registry:
    def __init__(self, adapter) -> None:
        self._adapter = adapter

    def for_backend(self, backend):
        return self._adapter


def _bundle(task_type: str = "refactor", lane: LaneName = LaneName.CLAUDE_CLI) -> ProposalDecisionBundle:
    from operations_center.contracts.routing import LaneDecision
    proposal = build_proposal(
        PlanningContext(
            goal_text="Refactor the foo module",
            task_type=task_type,
            repo_key="svc",
            clone_url="https://example.invalid/svc.git",
        )
    )
    return ProposalDecisionBundle(
        proposal=proposal,
        decision=LaneDecision(
            proposal_id=proposal.proposal_id,
            selected_lane=lane,
            selected_backend=BackendName.KODO,
        ),
    )


def _runtime(binding: RuntimeBindingSummary | None = None) -> ExecutionRuntimeContext:
    return ExecutionRuntimeContext(
        workspace_path=Path("/tmp/workspace"),
        task_branch="auto/refactor",
        runtime_binding=binding,
    )


def _success_result(bundle: ProposalDecisionBundle) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )


def _coordinator(adapter, policy_engine, runtime_binding_policy=None):
    return ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=policy_engine,
        runtime_binding_policy=runtime_binding_policy,
    )


def _allow() -> _StubPolicyEngine:
    return _StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRuntimeBindingPolicyWiring:
    def test_no_policy_no_binding_passthrough(self):
        """When no policy and no caller-supplied binding, runtime_binding stays None."""
        bundle = _bundle()
        adapter = _RecordingAdapter(_success_result(bundle))
        coord = _coordinator(adapter, _allow(), runtime_binding_policy=None)

        coord.execute(bundle, _runtime())

        assert adapter.last_request.runtime_binding is None

    def test_policy_populates_binding_for_matching_rule(self):
        """A matching rule populates runtime_binding on the request."""
        bundle = _bundle(task_type="refactor", lane=LaneName.CLAUDE_CLI)
        adapter = _RecordingAdapter(_success_result(bundle))
        coord = _coordinator(adapter, _allow(), runtime_binding_policy=DEFAULT_POLICY)

        coord.execute(bundle, _runtime())

        rb = adapter.last_request.runtime_binding
        assert rb is not None
        assert rb.kind == "cli_subscription"
        assert rb.provider == "anthropic"
        assert rb.model == "opus"  # refactor + claude_cli → opus per DEFAULT_POLICY
        assert rb.selection_mode == "policy_selected"

    def test_policy_default_used_when_no_rule_matches(self):
        """Catch-all default produces a binding when no rule matches."""
        # Use a task_type/lane combo that none of the named rules cover
        bundle = _bundle(task_type="refactor", lane=LaneName.AIDER_LOCAL)
        adapter = _RecordingAdapter(_success_result(bundle))
        coord = _coordinator(adapter, _allow(), runtime_binding_policy=DEFAULT_POLICY)

        coord.execute(bundle, _runtime())

        rb = adapter.last_request.runtime_binding
        assert rb is not None
        assert rb.model == "sonnet"  # default

    def test_caller_supplied_binding_wins_over_policy(self):
        """If runtime_binding is already set on the runtime context, the policy MUST NOT override."""
        bundle = _bundle(task_type="refactor", lane=LaneName.CLAUDE_CLI)
        adapter = _RecordingAdapter(_success_result(bundle))
        coord = _coordinator(adapter, _allow(), runtime_binding_policy=DEFAULT_POLICY)

        explicit = RuntimeBindingSummary(
            kind="cli_subscription",
            selection_mode="explicit_request",
            provider="anthropic",
            model="haiku",
        )
        coord.execute(bundle, _runtime(binding=explicit))

        rb = adapter.last_request.runtime_binding
        assert rb is not None
        assert rb.model == "haiku"  # caller-pinned, not policy's "opus"
        assert rb.selection_mode == "explicit_request"

    def test_empty_policy_leaves_binding_none(self):
        """A policy with no rules and no default behaves like no policy at all."""
        bundle = _bundle()
        adapter = _RecordingAdapter(_success_result(bundle))
        empty = RuntimeBindingPolicy(rules=())
        coord = _coordinator(adapter, _allow(), runtime_binding_policy=empty)

        coord.execute(bundle, _runtime())

        assert adapter.last_request.runtime_binding is None

    def test_policy_failure_falls_back_to_passthrough(self):
        """If the policy raises, the run still proceeds with no binding."""

        class _BoomPolicy:
            def select(self, proposal, decision):
                raise RuntimeError("policy down")

        bundle = _bundle()
        adapter = _RecordingAdapter(_success_result(bundle))
        coord = _coordinator(adapter, _allow(), runtime_binding_policy=_BoomPolicy())

        coord.execute(bundle, _runtime())

        # Run completed, no binding set
        assert adapter.last_request.runtime_binding is None
