# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
routing/client.py — LaneRoutingClient protocol and concrete routing clients.

OperationsCenter's supported routing path crosses the SwitchBoard service boundary
over HTTP.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

import httpx

from operations_center.contracts.cxrp_mapper import from_cxrp_lane_decision
from operations_center.contracts.proposal import TaskProposal
from operations_center.contracts.routing import LaneDecision

DEFAULT_SWITCHBOARD_URL = "http://localhost:20401"


class SwitchBoardUnavailableError(RuntimeError):
    """Raised when the SwitchBoard service cannot be reached or times out.

    Set OPERATIONS_CENTER_SWITCHBOARD_URL and ensure SwitchBoard is running.
    """


@runtime_checkable
class LaneRoutingClient(Protocol):
    """Boundary: TaskProposal in, LaneDecision out.

    Implementations may call SwitchBoard over HTTP or apply a test stub.
    The rest of OperationsCenter sees only this interface.
    """

    def select_lane(self, proposal: TaskProposal) -> LaneDecision:
        ...


class HttpLaneRoutingClient:
    """Routes proposals through the SwitchBoard HTTP service boundary.

    This is the canonical OperationsCenter -> SwitchBoard integration path.
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
        try:
            response = self._client.post("/route", json=proposal.model_dump(mode="json"))
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise SwitchBoardUnavailableError(
                f"SwitchBoard unreachable at {self.base_url}. "
                "Set OPERATIONS_CENTER_SWITCHBOARD_URL or start the SwitchBoard service. "
                f"Cause: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise SwitchBoardUnavailableError(
                f"SwitchBoard request timed out at {self.base_url}. "
                f"Cause: {exc}"
            ) from exc
        return _decode_route_response(response.json())

    def close(self) -> None:
        self._client.close()

    @classmethod
    def from_env(cls) -> "HttpLaneRoutingClient":
        base_url = os.environ.get("OPERATIONS_CENTER_SWITCHBOARD_URL") or DEFAULT_SWITCHBOARD_URL
        return cls(base_url=base_url)


class StubLaneRoutingClient:
    """Always returns a fixed LaneDecision. For unit tests only.

    Construct with a pre-built LaneDecision to inject deterministic routing
    without touching SwitchBoard at all.
    """

    def __init__(self, decision: LaneDecision) -> None:
        self._decision = decision

    def select_lane(self, proposal: TaskProposal) -> LaneDecision:  # noqa: ARG002 - stub ignores arg
        return self._decision


def _decode_route_response(payload: dict) -> LaneDecision:
    """Decode SwitchBoard's /route response into an OC LaneDecision.

    SwitchBoard emits the CxRP v0.2 envelope (``contract_kind ==
    "lane_decision"``, ``schema_version`` starting with ``"0."``). The
    transitional fallback to OC's rich Pydantic shape has been removed
    now that the wire flip is complete; an unrecognised shape raises
    ``ValueError`` rather than silently mis-parsing it.
    """
    if (
        payload.get("contract_kind") == "lane_decision"
        and payload.get("schema_version", "").startswith("0.")
    ):
        return from_cxrp_lane_decision(payload)
    raise ValueError(
        "Unexpected /route response shape: expected CxRP LaneDecision "
        "envelope (contract_kind='lane_decision', schema_version='0.x'). "
        f"Got contract_kind={payload.get('contract_kind')!r}, "
        f"schema_version={payload.get('schema_version')!r}."
    )
