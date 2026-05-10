# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for routing/client.py."""

from __future__ import annotations

import json

import httpx
import pytest

from operations_center.contracts.enums import BackendName, LaneName
from operations_center.contracts.routing import LaneDecision
from operations_center.planning.models import PlanningContext
from operations_center.planning.proposal_builder import build_proposal
from operations_center.routing.client import (
    DEFAULT_SWITCHBOARD_URL,
    HttpLaneRoutingClient,
    LaneRoutingClient,
    StubLaneRoutingClient,
    SwitchBoardUnavailableError,
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


def _stub_cxrp_response(executor: str = "claude_cli", backend: str = "kodo") -> dict:
    """Minimal CxRP v0.2 LaneDecision payload for /route mock responses."""
    return {
        "schema_version": "0.3",
        "contract_kind": "lane_decision",
        "decision_id": "dec-test-1",
        "proposal_id": "prop-test-1",
        "metadata": {"policy_rule_matched": "test_rule"},
        "lane": "coding_agent",
        "executor": executor,
        "backend": backend,
        "confidence": 0.9,
        "rationale": "stub",
        "alternatives": [],
    }


def test_http_client_satisfies_protocol() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_stub_cxrp_response()))
    client = HttpLaneRoutingClient("http://switchboard.local", transport=transport)
    try:
        assert isinstance(client, LaneRoutingClient)
    finally:
        client.close()


def test_stub_client_satisfies_protocol() -> None:
    stub = StubLaneRoutingClient(_stub_decision())
    assert isinstance(stub, LaneRoutingClient)


def test_http_client_posts_to_route_endpoint() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_stub_cxrp_response())

    client = HttpLaneRoutingClient("http://switchboard.local", transport=httpx.MockTransport(handler))
    proposal = build_proposal(_ctx())
    try:
        decision = client.select_lane(proposal)
    finally:
        client.close()

    assert decision.selected_lane == LaneName.CLAUDE_CLI
    assert captured[0].method == "POST"
    assert str(captured[0].url) == "http://switchboard.local/route"


def test_http_client_serializes_canonical_proposal() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_stub_cxrp_response())

    client = HttpLaneRoutingClient("http://switchboard.local", transport=httpx.MockTransport(handler))
    proposal = build_proposal(_ctx(task_id="TASK-9", project_id="proj-9"))
    try:
        client.select_lane(proposal)
    finally:
        client.close()

    assert seen["task_id"] == "TASK-9"
    assert seen["project_id"] == "proj-9"
    assert seen["goal_text"] == "Fix lint errors in src/"


def test_http_client_from_env_prefers_operations_center_specific_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_SWITCHBOARD_URL", "http://sb.internal:20401")
    client = HttpLaneRoutingClient.from_env()
    try:
        assert client.base_url == "http://sb.internal:20401"
    finally:
        client.close()


def test_http_client_from_env_falls_back_to_default_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPERATIONS_CENTER_SWITCHBOARD_URL", raising=False)
    client = HttpLaneRoutingClient.from_env()
    try:
        assert client.base_url == DEFAULT_SWITCHBOARD_URL
    finally:
        client.close()


def test_stub_returns_fixed_decision() -> None:
    decision = _stub_decision(lane=LaneName.AIDER_LOCAL, backend=BackendName.DIRECT_LOCAL)
    stub = StubLaneRoutingClient(decision)
    proposal = build_proposal(_ctx())
    result = stub.select_lane(proposal)
    assert result is decision


# ---------------------------------------------------------------------------
# Error handling — SwitchBoard unavailable
# ---------------------------------------------------------------------------


def test_http_client_decodes_cxrp_shape_response() -> None:
    """SwitchBoard emits CxRP v0.2 envelope; the client must deserialize it
    back into the OC LaneDecision."""
    cxrp_payload = {
        "schema_version": "0.3",
        "contract_kind": "lane_decision",
        "decision_id": "dec-cxrp-1",
        "proposal_id": "prop-cxrp-1",
        "metadata": {"policy_rule_matched": "test_rule"},
        "lane": "coding_agent",
        "executor": "claude_cli",
        "backend": "kodo",
        "rationale": "cxrp-shape decision",
        "confidence": 0.88,
        "alternatives": [
            {"lane": "coding_agent", "executor": "codex_cli"}
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=cxrp_payload)

    client = HttpLaneRoutingClient("http://switchboard.local", transport=httpx.MockTransport(handler))
    proposal = build_proposal(_ctx())
    try:
        decision = client.select_lane(proposal)
    finally:
        client.close()

    assert decision.selected_lane == LaneName.CLAUDE_CLI
    assert decision.selected_backend == BackendName.KODO
    assert decision.confidence == 0.88
    assert decision.policy_rule_matched == "test_rule"
    assert decision.alternatives_considered == [LaneName.CODEX_CLI]


def test_http_client_rejects_legacy_oc_shape_response() -> None:
    """The transitional fallback to OC's rich Pydantic shape was removed
    after the wire flip; an unrecognised payload now raises ValueError
    rather than silently mis-parsing."""

    def handler(request: httpx.Request) -> httpx.Response:
        # OC's rich Pydantic dump — no schema_version/contract_kind keys.
        return httpx.Response(200, json=_stub_decision().model_dump(mode="json"))

    client = HttpLaneRoutingClient("http://switchboard.local", transport=httpx.MockTransport(handler))
    proposal = build_proposal(_ctx())
    try:
        with pytest.raises(ValueError, match="CxRP LaneDecision envelope"):
            client.select_lane(proposal)
    finally:
        client.close()


def test_connect_error_raises_switchboard_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    client = HttpLaneRoutingClient("http://switchboard.local", transport=httpx.MockTransport(handler))
    proposal = build_proposal(_ctx())
    try:
        with pytest.raises(SwitchBoardUnavailableError, match="unreachable"):
            client.select_lane(proposal)
    finally:
        client.close()


def test_timeout_raises_switchboard_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Request timed out")

    client = HttpLaneRoutingClient("http://switchboard.local", transport=httpx.MockTransport(handler))
    proposal = build_proposal(_ctx())
    try:
        with pytest.raises(SwitchBoardUnavailableError, match="timed out"):
            client.select_lane(proposal)
    finally:
        client.close()


def test_http_4xx_raises_http_status_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "validation error"})

    client = HttpLaneRoutingClient("http://switchboard.local", transport=httpx.MockTransport(handler))
    proposal = build_proposal(_ctx())
    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.select_lane(proposal)
    finally:
        client.close()


def test_switchboard_unavailable_error_wraps_cause() -> None:
    cause = httpx.ConnectError("ECONNREFUSED")

    def handler(request: httpx.Request) -> httpx.Response:
        raise cause

    client = HttpLaneRoutingClient("http://switchboard.local", transport=httpx.MockTransport(handler))
    proposal = build_proposal(_ctx())
    try:
        with pytest.raises(SwitchBoardUnavailableError) as exc_info:
            client.select_lane(proposal)
    finally:
        client.close()

    assert exc_info.value.__cause__ is cause
