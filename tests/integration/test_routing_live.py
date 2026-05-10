# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
Integration tests for OperationsCenter → SwitchBoard routing boundary.

These tests require a running SwitchBoard instance. They are skipped automatically
when SwitchBoard is unreachable. To run them:

    # Start the stack first (from WorkStation):
    ./scripts/up.sh

    # Then run from this repo:
    pytest tests/integration/ -v

Or target SwitchBoard explicitly:

    OPERATIONS_CENTER_SWITCHBOARD_URL=http://localhost:20401 pytest tests/integration/ -v
"""

from __future__ import annotations

import pytest

from operations_center.contracts.enums import LaneName, BackendName
from operations_center.contracts.routing import LaneDecision
from operations_center.planning.models import PlanningContext
from operations_center.planning.proposal_builder import build_proposal
from operations_center.routing.client import HttpLaneRoutingClient, SwitchBoardUnavailableError


def _ctx(**kw) -> PlanningContext:
    defaults = dict(
        goal_text="Fix lint errors in src/",
        task_type="lint_fix",
        repo_key="api-service",
        clone_url="https://github.com/org/api-service.git",
        risk_level="low",
        priority="normal",
    )
    defaults.update(kw)
    return PlanningContext(**defaults)


# ---------------------------------------------------------------------------
# Canonical routing boundary: TaskProposal -> SwitchBoard -> LaneDecision
# ---------------------------------------------------------------------------


def test_canonical_proposal_returns_lane_decision(switchboard_url: str) -> None:
    """OperationsCenter sends a TaskProposal; SwitchBoard returns a valid LaneDecision."""
    client = HttpLaneRoutingClient(switchboard_url)
    proposal = build_proposal(_ctx())
    try:
        decision = client.select_lane(proposal)
    finally:
        client.close()

    assert isinstance(decision, LaneDecision)
    assert isinstance(decision.selected_lane, LaneName)
    assert isinstance(decision.selected_backend, BackendName)
    assert 0.0 <= decision.confidence <= 1.0


def test_response_validates_as_canonical_lane_decision(switchboard_url: str) -> None:
    """The LaneDecision returned by SwitchBoard is a fully valid Pydantic model."""
    client = HttpLaneRoutingClient(switchboard_url)
    proposal = build_proposal(_ctx(task_id="INTEG-1", project_id="cp-integ"))
    try:
        decision = client.select_lane(proposal)
    finally:
        client.close()

    # Round-trip through serialization to confirm the model is fully valid
    roundtripped = LaneDecision.model_validate(decision.model_dump(mode="json"))
    assert roundtripped == decision


def test_different_proposals_may_receive_decisions(switchboard_url: str) -> None:
    """Two distinct proposals both receive valid decisions (no crash on repeated calls)."""
    client = HttpLaneRoutingClient(switchboard_url)
    try:
        d1 = client.select_lane(build_proposal(_ctx(task_type="lint_fix", risk_level="low")))
        d2 = client.select_lane(build_proposal(_ctx(task_type="feature", risk_level="high")))
    finally:
        client.close()

    assert isinstance(d1, LaneDecision)
    assert isinstance(d2, LaneDecision)


# ---------------------------------------------------------------------------
# SwitchBoard unavailable failure path
# ---------------------------------------------------------------------------


def test_unreachable_switchboard_raises_unavailable_error() -> None:
    """When SwitchBoard is down, a clear SwitchBoardUnavailableError is raised."""
    client = HttpLaneRoutingClient("http://localhost:19999", timeout=2.0)
    proposal = build_proposal(_ctx())
    try:
        with pytest.raises(SwitchBoardUnavailableError, match="unreachable"):
            client.select_lane(proposal)
    finally:
        client.close()
