"""Tests for routing/service.py — PlanningService."""

from __future__ import annotations

import pytest

from control_plane.contracts.enums import BackendName, LaneName
from control_plane.contracts.routing import LaneDecision
from control_plane.planning.models import PlanningContext, ProposalDecisionBundle
from control_plane.routing.client import StubLaneRoutingClient
from control_plane.routing.service import PlanningService


def _ctx(**kw) -> PlanningContext:
    defaults = dict(
        goal_text="Fix lint errors in src/",
        task_type="lint_fix",
        repo_key="api-service",
        clone_url="https://github.com/org/api-service.git",
    )
    defaults.update(kw)
    return PlanningContext(**defaults)


def _stub_service(
    lane=LaneName.CLAUDE_CLI,
    backend=BackendName.KODO,
) -> PlanningService:
    decision = LaneDecision(
        proposal_id="",  # will be overwritten in real routing; stub ignores it
        selected_lane=lane,
        selected_backend=backend,
        confidence=0.9,
        policy_rule_matched="test_rule",
    )
    return PlanningService.with_client(StubLaneRoutingClient(decision))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_plan_returns_bundle():
    service = _stub_service()
    bundle = service.plan(_ctx())
    assert isinstance(bundle, ProposalDecisionBundle)


def test_bundle_has_proposal():
    service = _stub_service()
    ctx = _ctx(goal_text="Refactor login module")
    bundle = service.plan(ctx)
    assert bundle.proposal.goal_text == "Refactor login module"


def test_bundle_has_decision():
    service = _stub_service(lane=LaneName.AIDER_LOCAL)
    bundle = service.plan(_ctx())
    assert bundle.decision.selected_lane == LaneName.AIDER_LOCAL


def test_bundle_context_matches_input():
    service = _stub_service()
    ctx = _ctx()
    bundle = service.plan(ctx)
    assert bundle.context is ctx


def test_bundle_timestamp_set():
    service = _stub_service()
    bundle = service.plan(_ctx())
    assert bundle.bundled_at is not None


# ---------------------------------------------------------------------------
# trace_notes
# ---------------------------------------------------------------------------


def test_trace_notes_propagated():
    service = _stub_service()
    bundle = service.plan(_ctx(), trace_notes="triggered by cron job")
    assert bundle.trace_notes == "triggered by cron job"


def test_trace_notes_default_empty():
    service = _stub_service()
    bundle = service.plan(_ctx())
    assert bundle.trace_notes == ""


# ---------------------------------------------------------------------------
# run_summary
# ---------------------------------------------------------------------------


def test_run_summary_contains_lane():
    service = _stub_service(lane=LaneName.CLAUDE_CLI, backend=BackendName.KODO)
    bundle = service.plan(_ctx())
    assert "claude_cli" in bundle.run_summary


def test_run_summary_contains_backend():
    service = _stub_service(lane=LaneName.AIDER_LOCAL, backend=BackendName.KODO)
    bundle = service.plan(_ctx())
    assert "kodo" in bundle.run_summary


def test_run_summary_contains_proposal_id_prefix():
    service = _stub_service()
    bundle = service.plan(_ctx())
    assert bundle.proposal.proposal_id[:8] in bundle.run_summary


# ---------------------------------------------------------------------------
# Validation propagation
# ---------------------------------------------------------------------------


def test_invalid_context_raises():
    service = _stub_service()
    with pytest.raises(ValueError, match="goal_text"):
        service.plan(_ctx(goal_text="   "))


def test_invalid_repo_key_raises():
    service = _stub_service()
    with pytest.raises(ValueError, match="repo_key"):
        service.plan(_ctx(repo_key=""))


# ---------------------------------------------------------------------------
# Default constructor
# ---------------------------------------------------------------------------


def test_default_service_uses_real_routing():
    service = PlanningService.default()
    ctx = _ctx(task_type="lint_fix", risk_level="low")
    bundle = service.plan(ctx)
    # Real policy routes lint_fix/low to aider_local
    assert bundle.decision.selected_lane == LaneName.AIDER_LOCAL
    assert bundle.decision.selected_backend == BackendName.DIRECT_LOCAL


# ---------------------------------------------------------------------------
# Multiple proposals get unique IDs
# ---------------------------------------------------------------------------


def test_two_plans_produce_different_proposal_ids():
    service = _stub_service()
    b1 = service.plan(_ctx(goal_text="Fix lint"))
    b2 = service.plan(_ctx(goal_text="Fix lint"))
    assert b1.proposal.proposal_id != b2.proposal.proposal_id
