"""
Integration tests for the ControlPlane execution boundary.

These tests prove the full pipeline:

    PlanningContext
        → build_proposal() → TaskProposal
        → ExecutionRequestBuilder → ExecutionRequest
        → DirectLocalBackendAdapter → ExecutionResult

No live external service is required. The adapter is invoked directly:
- The missing-binary path proves the full boundary without needing aider installed.
- The aider-available path is skipped unless `which aider` succeeds.

Run from the ControlPlane repo:

    pytest tests/integration/test_execution_boundary.py -v
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from control_plane.backends.direct_local.adapter import DirectLocalBackendAdapter
from control_plane.config.settings import AiderSettings
from control_plane.contracts.enums import ExecutionStatus, FailureReasonCategory
from control_plane.contracts.execution import ExecutionResult
from control_plane.contracts.routing import LaneDecision
from control_plane.contracts.enums import BackendName, LaneName
from control_plane.execution.handoff import ExecutionRequestBuilder, ExecutionRuntimeContext
from control_plane.planning.models import PlanningContext, ProposalDecisionBundle
from control_plane.planning.proposal_builder import build_proposal


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_bundle() -> ProposalDecisionBundle:
    proposal = build_proposal(
        PlanningContext(
            goal_text="Fix all lint errors in src/",
            task_type="lint_fix",
            repo_key="test-repo",
            clone_url="https://example.invalid/test-repo.git",
            risk_level="low",
            priority="normal",
            timeout_seconds=30,
        )
    )
    decision = LaneDecision(
        proposal_id=proposal.proposal_id,
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=BackendName.DIRECT_LOCAL,
        confidence=0.9,
        policy_rule_matched="test_rule",
    )
    return ProposalDecisionBundle(proposal=proposal, decision=decision)


def _make_adapter(binary: str = "__nonexistent_binary__", timeout_seconds: int = 10) -> DirectLocalBackendAdapter:
    return DirectLocalBackendAdapter(
        AiderSettings(binary=binary, timeout_seconds=timeout_seconds)
    )


# ---------------------------------------------------------------------------
# Execution boundary: full pipeline (no live backend required)
# ---------------------------------------------------------------------------


def test_builder_produces_canonical_request_from_bundle(tmp_path: Path) -> None:
    """ExecutionRequestBuilder converts ProposalDecisionBundle → ExecutionRequest correctly."""
    bundle = _make_bundle()
    runtime = ExecutionRuntimeContext(
        workspace_path=tmp_path / "workspace",
        task_branch="auto/lint-abc",
    )
    request = ExecutionRequestBuilder().build(bundle, runtime)

    assert request.proposal_id == bundle.proposal.proposal_id
    assert request.decision_id == bundle.decision.decision_id
    assert request.goal_text == "Fix all lint errors in src/"
    assert request.repo_key == "test-repo"
    assert request.task_branch == "auto/lint-abc"
    assert request.timeout_seconds == 30


def test_adapter_returns_canonical_result_for_missing_binary(tmp_path: Path) -> None:
    """DirectLocalBackendAdapter returns a canonical ExecutionResult when binary is missing."""
    bundle = _make_bundle()
    runtime = ExecutionRuntimeContext(
        workspace_path=tmp_path / "workspace",
        task_branch="auto/lint-abc",
    )
    request = ExecutionRequestBuilder().build(bundle, runtime)
    result = _make_adapter().execute(request)

    assert isinstance(result, ExecutionResult)
    assert result.success is False
    assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert result.proposal_id == bundle.proposal.proposal_id
    assert result.decision_id == bundle.decision.decision_id


def test_full_boundary_pipeline_missing_binary(tmp_path: Path) -> None:
    """Full pipeline: PlanningContext → TaskProposal → ExecutionRequest → ExecutionResult."""
    bundle = _make_bundle()
    runtime = ExecutionRuntimeContext(
        workspace_path=tmp_path / "workspace",
        task_branch="auto/lint-abc",
    )

    request = ExecutionRequestBuilder().build(bundle, runtime)
    result = _make_adapter().execute(request)

    # Verify contract chain: proposal_id and decision_id flow end-to-end
    assert result.proposal_id == bundle.proposal.proposal_id
    assert result.decision_id == bundle.decision.decision_id

    # Verify result is fully serialisable (canonical contract)
    import json
    payload = json.loads(result.model_dump_json())
    assert payload["success"] is False
    assert payload["failure_category"] == "backend_error"


def test_result_carries_no_routing_fields(tmp_path: Path) -> None:
    """ExecutionResult must not contain routing-only fields."""
    bundle = _make_bundle()
    runtime = ExecutionRuntimeContext(workspace_path=tmp_path / "workspace", task_branch="auto/x")
    request = ExecutionRequestBuilder().build(bundle, runtime)
    result = _make_adapter().execute(request)

    assert not hasattr(result, "selected_lane")
    assert not hasattr(result, "policy_rule_matched")


# ---------------------------------------------------------------------------
# Aider-available path (skipped if aider not installed)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def aider_binary() -> str:
    binary = shutil.which("aider")
    if binary is None:
        pytest.skip("aider not installed — skipping live adapter test")
    return binary


def test_aider_adapter_returns_result_for_trivial_goal(tmp_path: Path, aider_binary: str) -> None:
    """When aider is installed, the adapter runs and returns a canonical result."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    bundle = _make_bundle()
    runtime = ExecutionRuntimeContext(
        workspace_path=workspace,
        task_branch="auto/lint-abc",
    )
    request = ExecutionRequestBuilder().build(bundle, runtime)
    adapter = _make_adapter(binary=aider_binary, timeout_seconds=60)
    result = adapter.execute(request)

    assert isinstance(result, ExecutionResult)
    assert result.proposal_id == bundle.proposal.proposal_id
    # Status may be SUCCESS or FAILED depending on aider's result — both are valid
    assert result.status in {ExecutionStatus.SUCCESS, ExecutionStatus.FAILED}
