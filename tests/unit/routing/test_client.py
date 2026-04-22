"""Tests for routing/client.py."""

from __future__ import annotations

import json

import httpx
import pytest

from control_plane.contracts.enums import BackendName, LaneName
from control_plane.contracts.routing import LaneDecision
from control_plane.planning.models import PlanningContext
from control_plane.planning.proposal_builder import build_proposal
from control_plane.routing.client import (
    DEFAULT_SWITCHBOARD_URL,
    HttpLaneRoutingClient,
    LaneRoutingClient,
    StubLaneRoutingClient,
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


def test_http_client_satisfies_protocol() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_stub_decision().model_dump(mode="json")))
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
        return httpx.Response(200, json=_stub_decision().model_dump(mode="json"))

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
        return httpx.Response(200, json=_stub_decision().model_dump(mode="json"))

    client = HttpLaneRoutingClient("http://switchboard.local", transport=httpx.MockTransport(handler))
    proposal = build_proposal(_ctx(task_id="TASK-9", project_id="proj-9"))
    try:
        client.select_lane(proposal)
    finally:
        client.close()

    assert seen["task_id"] == "TASK-9"
    assert seen["project_id"] == "proj-9"
    assert seen["goal_text"] == "Fix lint errors in src/"


def test_http_client_from_env_prefers_control_plane_specific_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_SWITCHBOARD_URL", "http://sb.internal:20401")
    client = HttpLaneRoutingClient.from_env()
    try:
        assert client.base_url == "http://sb.internal:20401"
    finally:
        client.close()


def test_http_client_from_env_falls_back_to_default_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTROL_PLANE_SWITCHBOARD_URL", raising=False)
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
