# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""G-V02 — SwitchBoard routing provenance must reach ExecutionRecord.metadata.

The execution_record.json artifact must surface the routing decision so an
audit consumer can answer "which rule fired? why? from which switchboard
version?" without having to re-read decision.json.
"""

from __future__ import annotations

from operations_center.contracts.enums import BackendName, LaneName
from operations_center.contracts.routing import LaneDecision
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.planning.models import ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.planning.models import PlanningContext
from operations_center.policy.models import PolicyDecision, PolicyStatus

# Reuse the existing coordinator test fixtures.
from tests.unit.execution.test_coordinator import (  # noqa: E402
    _RecordingAdapter,
    _Registry,
    _StubPolicyEngine,
    _runtime,
    _success_result,
)


def _bundle_with_rich_decision() -> ProposalDecisionBundle:
    proposal = build_proposal(
        PlanningContext(
            goal_text="Add G-V02 routing block",
            task_type="lint_fix",
            repo_key="svc",
            clone_url="https://example.invalid/svc.git",
        )
    )
    decision = LaneDecision(
        proposal_id=proposal.proposal_id,
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=BackendName.DIRECT_LOCAL,
        confidence=0.87,
        policy_rule_matched="lint_fix_to_aider_local",
        rationale="lint_fix tasks default to aider_local",
        alternatives_considered=[LaneName.CLAUDE_CLI],
        switchboard_version="0.4.2",
    )
    return ProposalDecisionBundle(proposal=proposal, decision=decision)


def test_routing_provenance_lands_in_record_metadata() -> None:
    bundle = _bundle_with_rich_decision()
    adapter = _RecordingAdapter(_success_result(bundle))
    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
    )

    outcome = coordinator.execute(bundle, _runtime())

    routing = outcome.record.metadata["routing"]
    assert routing["decision_id"] == bundle.decision.decision_id
    assert routing["selected_lane"] == "aider_local"
    assert routing["selected_backend"] == "direct_local"
    assert routing["policy_rule_matched"] == "lint_fix_to_aider_local"
    assert routing["rationale"] == "lint_fix tasks default to aider_local"
    assert routing["switchboard_version"] == "0.4.2"
    assert routing["confidence"] == 0.87
    assert routing["alternatives_considered"] == ["claude_cli"]


def test_routing_block_present_with_optional_fields_unset() -> None:
    """Decisions that omit rule/rationale/version still produce the routing block."""
    bundle = _bundle_with_rich_decision()
    bare = LaneDecision(
        proposal_id=bundle.proposal.proposal_id,
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=BackendName.DIRECT_LOCAL,
    )
    bundle = ProposalDecisionBundle(proposal=bundle.proposal, decision=bare)

    adapter = _RecordingAdapter(_success_result(bundle))
    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
    )

    outcome = coordinator.execute(bundle, _runtime())

    routing = outcome.record.metadata["routing"]
    assert routing["decision_id"] == bare.decision_id
    assert routing["policy_rule_matched"] is None
    assert routing["rationale"] is None
    assert routing["switchboard_version"] is None
    assert routing["alternatives_considered"] == []
