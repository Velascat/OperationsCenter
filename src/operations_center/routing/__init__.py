# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
routing/ — OperationsCenter's integration boundary with SwitchBoard.

Public API:
    LaneRoutingClient   — protocol (for type hints and test stubs)
    HttpLaneRoutingClient — default implementation (out-of-process HTTP client)
    StubLaneRoutingClient  — test stub (inject a fixed OC routing decision)
    PlanningService     — build_proposal(context), route_proposal(proposal), and plan(context)
"""

from .client import (
    HttpLaneRoutingClient,
    LaneRoutingClient,
    StubLaneRoutingClient,
    SwitchBoardUnavailableError,
)
from .service import PlanningService

__all__ = [
    "HttpLaneRoutingClient",
    "LaneRoutingClient",
    "StubLaneRoutingClient",
    "SwitchBoardUnavailableError",
    "PlanningService",
]
