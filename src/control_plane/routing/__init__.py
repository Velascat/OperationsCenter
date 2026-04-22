"""
routing/ — ControlPlane's integration boundary with SwitchBoard.

Public API:
    LaneRoutingClient   — protocol (for type hints and test stubs)
    LocalLaneRoutingClient — default implementation (in-process via Python import)
    StubLaneRoutingClient  — test stub (inject a fixed LaneDecision)
    PlanningService     — plan(context) → ProposalDecisionBundle
"""

from .client import LaneRoutingClient, LocalLaneRoutingClient, StubLaneRoutingClient
from .service import PlanningService

__all__ = [
    "LaneRoutingClient",
    "LocalLaneRoutingClient",
    "StubLaneRoutingClient",
    "PlanningService",
]
