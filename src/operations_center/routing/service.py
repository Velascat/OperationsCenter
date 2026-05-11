# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
routing/service.py — explicit proposal-build and routing surfaces.

PlanningService exposes the two real stages in OperationsCenter's planning and
routing pipeline:

  1. build_proposal(context) -> OcPlanningProposal
  2. route_proposal(proposal) -> ProposalDecisionBundle

plan() remains as a thin convenience wrapper for callers that want both steps
in one call. The service owns no routing logic — all policy lives in
SwitchBoard.
"""

from __future__ import annotations

from typing import Optional

from operations_center.contracts.proposal import OcPlanningProposal
from operations_center.planning.models import (
    PlanningContext,
    ProposalDecisionBundle,
)
from operations_center.planning.proposal_builder import build_proposal

from .client import HttpLaneRoutingClient, LaneRoutingClient


class PlanningService:
    """Orchestrates proposal building and lane routing.

    Typical usage:

        service = PlanningService.default()
        proposal = service.build_proposal(context)
        bundle = service.route_proposal(proposal, context=context)
        # bundle.proposal  → OcPlanningProposal
        # bundle.decision  → OcRoutingDecision
        # bundle.run_summary → trace string
    """

    def __init__(self, routing_client: LaneRoutingClient) -> None:
        self._client = routing_client

    def build_proposal(self, context: PlanningContext) -> OcPlanningProposal:
        """Build an OC planning proposal from PlanningContext."""
        return build_proposal(context)

    def route_proposal(
        self,
        proposal: OcPlanningProposal,
        *,
        context: Optional[PlanningContext] = None,
        trace_notes: str = "",
    ) -> ProposalDecisionBundle:
        """Route an OC planning proposal across the SwitchBoard service boundary."""
        decision = self._client.select_lane(proposal)
        return ProposalDecisionBundle(
            proposal=proposal,
            decision=decision,
            context=context,
            trace_notes=trace_notes,
        )

    def plan(
        self,
        context: PlanningContext,
        trace_notes: str = "",
    ) -> ProposalDecisionBundle:
        """Convenience wrapper: build a proposal, then route that proposal.

        Raises:
            ValueError: if context validation fails (propagated from build_proposal).
        """
        proposal = self.build_proposal(context)
        return self.route_proposal(proposal, context=context, trace_notes=trace_notes)

    @classmethod
    def default(cls) -> "PlanningService":
        """Create a PlanningService backed by the SwitchBoard HTTP client."""
        return cls(routing_client=HttpLaneRoutingClient.from_env())

    @classmethod
    def with_client(cls, client: LaneRoutingClient) -> "PlanningService":
        return cls(routing_client=client)
