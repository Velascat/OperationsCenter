"""
routing/service.py — PlanningService: PlanningContext → ProposalDecisionBundle.

PlanningService is the single entry point for ControlPlane's planning and
routing pipeline. It:

  1. Validates and translates PlanningContext into a canonical TaskProposal
     (via ProposalBuilder).
  2. Routes the proposal through SwitchBoard (via LaneRoutingClient) to get a
     LaneDecision.
  3. Bundles both into a ProposalDecisionBundle for downstream execution.

The service owns no routing logic — all policy lives in SwitchBoard.
"""

from __future__ import annotations

from typing import Optional

from control_plane.planning.models import (
    PlanningContext,
    ProposalDecisionBundle,
)
from control_plane.planning.proposal_builder import build_proposal

from .client import LaneRoutingClient, LocalLaneRoutingClient


class PlanningService:
    """Orchestrates proposal building and lane routing.

    Typical usage:

        service = PlanningService.default()
        bundle = service.plan(context)
        # bundle.proposal  → TaskProposal
        # bundle.decision  → LaneDecision
        # bundle.run_summary → trace string
    """

    def __init__(self, routing_client: LaneRoutingClient) -> None:
        self._client = routing_client

    def plan(
        self,
        context: PlanningContext,
        trace_notes: str = "",
    ) -> ProposalDecisionBundle:
        """Build a TaskProposal from context and route it to get a LaneDecision.

        Raises:
            ValueError: if context validation fails (propagated from build_proposal).
        """
        proposal = build_proposal(context)
        decision = self._client.select_lane(proposal)
        return ProposalDecisionBundle(
            proposal=proposal,
            decision=decision,
            context=context,
            trace_notes=trace_notes,
        )

    @classmethod
    def default(cls) -> "PlanningService":
        """Create a PlanningService backed by LocalLaneRoutingClient."""
        return cls(routing_client=LocalLaneRoutingClient.with_default_policy())

    @classmethod
    def with_client(cls, client: LaneRoutingClient) -> "PlanningService":
        return cls(routing_client=client)
