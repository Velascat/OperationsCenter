"""
routing/ — ControlPlane's integration boundary with SwitchBoard.

Public API:
    LaneRoutingClient   — protocol (for type hints and test stubs)
    HttpLaneRoutingClient — default implementation (out-of-process HTTP client)
    StubLaneRoutingClient  — test stub (inject a fixed LaneDecision)
    PlanningService     — plan(context) → ProposalDecisionBundle
"""

from .client import HttpLaneRoutingClient, LaneRoutingClient, StubLaneRoutingClient
from .service import PlanningService

__all__ = [
    "HttpLaneRoutingClient",
    "LaneRoutingClient",
    "StubLaneRoutingClient",
    "PlanningService",
]
