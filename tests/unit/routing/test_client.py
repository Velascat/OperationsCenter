"""Tests for routing/client.py."""

from __future__ import annotations

import pytest

from control_plane.contracts.enums import BackendName, LaneName
from control_plane.contracts.routing import LaneDecision
from control_plane.planning.models import PlanningContext
from control_plane.planning.proposal_builder import build_proposal
from control_plane.routing.client import (
    LaneRoutingClient,
    LocalLaneRoutingClient,
    StubLaneRoutingClient,
)


def _ctx(**kw) -> PlanningContext:
    defaults = dict(
        goal_text="Fix lint errors in src/",
        task_type="lint_fix",
        repo_key="api-service",
        clone_url="https://github.com/org/api-service.git",
    )
    defaults.update(kw)
    return PlanningContext(**defaults)


def _stub_decision(lane=LaneName.CLAUDE_CLI, backend=BackendName.KODO) -> LaneDecision:
    return LaneDecision(
        proposal_id="prop-test-1",
        selected_lane=lane,
        selected_backend=backend,
        confidence=0.9,
        policy_rule_matched="test_rule",
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_local_client_satisfies_protocol():
    client = LocalLaneRoutingClient.with_default_policy()
    assert isinstance(client, LaneRoutingClient)


def test_stub_client_satisfies_protocol():
    stub = StubLaneRoutingClient(_stub_decision())
    assert isinstance(stub, LaneRoutingClient)


# ---------------------------------------------------------------------------
# StubLaneRoutingClient
# ---------------------------------------------------------------------------


def test_stub_returns_fixed_decision():
    decision = _stub_decision(lane=LaneName.AIDER_LOCAL, backend=BackendName.DIRECT_LOCAL)
    stub = StubLaneRoutingClient(decision)
    proposal = build_proposal(_ctx())
    result = stub.select_lane(proposal)
    assert result is decision


def test_stub_same_decision_for_different_proposals():
    decision = _stub_decision()
    stub = StubLaneRoutingClient(decision)
    p1 = build_proposal(_ctx(task_type="lint_fix"))
    p2 = build_proposal(_ctx(task_type="refactor"))
    assert stub.select_lane(p1) is decision
    assert stub.select_lane(p2) is decision


# ---------------------------------------------------------------------------
# LocalLaneRoutingClient — real policy evaluation
# ---------------------------------------------------------------------------


def test_local_client_returns_lane_decision():
    client = LocalLaneRoutingClient.with_default_policy()
    proposal = build_proposal(_ctx(task_type="lint_fix", risk_level="low"))
    decision = client.select_lane(proposal)
    assert isinstance(decision, LaneDecision)
    assert decision.selected_lane in LaneName.__members__.values()
    assert decision.selected_backend in BackendName.__members__.values()


def test_local_client_routes_lint_fix_low_risk_to_aider_local():
    client = LocalLaneRoutingClient.with_default_policy()
    proposal = build_proposal(_ctx(task_type="lint_fix", risk_level="low"))
    decision = client.select_lane(proposal)
    assert decision.selected_lane == LaneName.AIDER_LOCAL


def test_local_client_routes_refactor_high_risk_to_claude_cli():
    client = LocalLaneRoutingClient.with_default_policy()
    proposal = build_proposal(_ctx(task_type="refactor", risk_level="high"))
    decision = client.select_lane(proposal)
    assert decision.selected_lane == LaneName.CLAUDE_CLI


def test_local_client_local_only_label_forces_aider_local():
    client = LocalLaneRoutingClient.with_default_policy()
    proposal = build_proposal(_ctx(
        task_type="refactor",
        risk_level="high",
        labels=["local_only"],
    ))
    decision = client.select_lane(proposal)
    assert decision.selected_lane == LaneName.AIDER_LOCAL


def test_local_client_decision_has_proposal_id():
    client = LocalLaneRoutingClient.with_default_policy()
    proposal = build_proposal(_ctx())
    decision = client.select_lane(proposal)
    assert decision.proposal_id == proposal.proposal_id


def test_local_client_different_proposals_different_decision_ids():
    client = LocalLaneRoutingClient.with_default_policy()
    p1 = build_proposal(_ctx(task_type="lint_fix"))
    p2 = build_proposal(_ctx(task_type="refactor"))
    d1 = client.select_lane(p1)
    d2 = client.select_lane(p2)
    assert d1.decision_id != d2.decision_id


# ---------------------------------------------------------------------------
# LocalLaneRoutingClient — custom policy injection
# ---------------------------------------------------------------------------


def test_local_client_accepts_custom_policy():
    from switchboard.lane.policy import (
        FallbackPolicy,
        LaneRoutingPolicy,
    )

    custom_policy = LaneRoutingPolicy(
        version="1",
        rules=[],
        backend_rules=[],
        fallback=FallbackPolicy(lane="aider_local", backend="direct_local"),
        thresholds={},
        excluded_backends=[],
    )
    client = LocalLaneRoutingClient(policy=custom_policy)
    proposal = build_proposal(_ctx())
    decision = client.select_lane(proposal)
    # No rules → fallback fires
    assert decision.selected_lane == LaneName.AIDER_LOCAL
