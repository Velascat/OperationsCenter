"""
routing/client.py — LaneRoutingClient protocol and concrete routing clients.

ControlPlane's supported routing path crosses the SwitchBoard service boundary
over HTTP.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

import httpx

from control_plane.contracts.proposal import TaskProposal
from control_plane.contracts.routing import LaneDecision

DEFAULT_SWITCHBOARD_URL = "http://localhost:20401"


@runtime_checkable
class LaneRoutingClient(Protocol):
    """Boundary: TaskProposal in, LaneDecision out.

    Implementations may call SwitchBoard over HTTP or apply a test stub.
    The rest of ControlPlane sees only this interface.
    """

    def select_lane(self, proposal: TaskProposal) -> LaneDecision:
        ...


class HttpLaneRoutingClient:
    """Routes proposals through the SwitchBoard HTTP service boundary.

    This is the canonical ControlPlane -> SwitchBoard integration path.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers={"Content-Type": "application/json"},
        )

    def select_lane(self, proposal: TaskProposal) -> LaneDecision:
        response = self._client.post("/route", json=proposal.model_dump(mode="json"))
        response.raise_for_status()
        return LaneDecision.model_validate(response.json())

    def close(self) -> None:
        self._client.close()

    @classmethod
    def from_env(cls) -> "HttpLaneRoutingClient":
        base_url = os.environ.get("CONTROL_PLANE_SWITCHBOARD_URL") or DEFAULT_SWITCHBOARD_URL
        return cls(base_url=base_url)


class StubLaneRoutingClient:
    """Always returns a fixed LaneDecision. For unit tests only.

    Construct with a pre-built LaneDecision to inject deterministic routing
    without touching SwitchBoard at all.
    """

    def __init__(self, decision: LaneDecision) -> None:
        self._decision = decision

    def select_lane(self, proposal: TaskProposal) -> LaneDecision:
        return self._decision
