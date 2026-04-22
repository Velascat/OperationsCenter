"""
routing/client.py — LaneRoutingClient protocol and LocalLaneRoutingClient implementation.

ControlPlane uses this boundary to route TaskProposals to SwitchBoard without
depending on SwitchBoard internals elsewhere. The protocol isolates the call
mechanism so callers never know if routing is local or over HTTP.

LocalLaneRoutingClient imports LaneSelector directly (switchboard installed
editable in the ControlPlane venv). It is the default implementation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from control_plane.contracts.proposal import TaskProposal
from control_plane.contracts.routing import LaneDecision


@runtime_checkable
class LaneRoutingClient(Protocol):
    """Boundary: TaskProposal in, LaneDecision out.

    Implementations may call SwitchBoard over HTTP, invoke it locally via
    Python import, or apply a test stub. The rest of ControlPlane sees only
    this interface.
    """

    def select_lane(self, proposal: TaskProposal) -> LaneDecision:
        ...


class LocalLaneRoutingClient:
    """Routes proposals through SwitchBoard's LaneSelector in-process.

    Requires the `switchboard` package to be installed in the current venv.
    Use this in production when ControlPlane and SwitchBoard share a Python
    environment, and in tests when you want real policy evaluation without an
    HTTP server.
    """

    def __init__(self, policy=None) -> None:
        from switchboard.lane.engine import LaneSelector

        self._selector = LaneSelector(policy=policy)

    def select_lane(self, proposal: TaskProposal) -> LaneDecision:
        return self._selector.select(proposal)

    @classmethod
    def with_default_policy(cls) -> "LocalLaneRoutingClient":
        return cls(policy=None)


class StubLaneRoutingClient:
    """Always returns a fixed LaneDecision. For unit tests only.

    Construct with a pre-built LaneDecision to inject deterministic routing
    without touching SwitchBoard at all.
    """

    def __init__(self, decision: LaneDecision) -> None:
        self._decision = decision

    def select_lane(self, proposal: TaskProposal) -> LaneDecision:
        return self._decision
