# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Invariant tests for OC internal contract ownership vs CxRP wire ownership."""

from __future__ import annotations

from pathlib import Path

from cxrp.contracts import ExecutionRequest as CxrpExecutionRequest
from cxrp.contracts import ExecutionResult as CxrpExecutionResult
from cxrp.contracts import LaneDecision as CxrpLaneDecision
from cxrp.contracts import TaskProposal as CxrpTaskProposal
from operations_center.contracts.cxrp_mapper import (
    to_cxrp_execution_request,
    to_cxrp_execution_result,
    from_cxrp_lane_decision,
    to_cxrp_lane_decision,
    to_cxrp_task_proposal,
)
from operations_center.contracts.proposal import OcPlanningProposal, TaskProposal
from operations_center.contracts.routing import LaneDecision, OcRoutingDecision
from operations_center.contracts.common import BranchPolicy, ExecutionConstraints, TaskTarget, ValidationProfile
from operations_center.contracts.enums import BackendName, ExecutionMode, LaneName, Priority, RiskLevel, TaskType
from operations_center.contracts.execution import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionArtifact,
    OcExecutionRequest,
    OcExecutionResult,
)
from operations_center.contracts.enums import ArtifactType, ExecutionStatus, ValidationStatus
from operations_center.contracts.common import ValidationSummary


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _proposal() -> OcPlanningProposal:
    return OcPlanningProposal(
        task_id="TASK-1",
        project_id="proj-1",
        task_type=TaskType.BUG_FIX,
        execution_mode=ExecutionMode.GOAL,
        goal_text="Fix serializer null handling.",
        target=TaskTarget(
            repo_key="svc",
            clone_url="https://github.com/ProtocolWarden/svc.git",
            base_branch="main",
        ),
        priority=Priority.NORMAL,
        risk_level=RiskLevel.LOW,
        constraints=ExecutionConstraints(),
        validation_profile=ValidationProfile(profile_name="default"),
        branch_policy=BranchPolicy(),
    )


def _decision(proposal_id: str) -> OcRoutingDecision:
    return OcRoutingDecision(
        proposal_id=proposal_id,
        selected_lane=LaneName.CLAUDE_CLI,
        selected_backend=BackendName.KODO,
        confidence=0.9,
        policy_rule_matched="default",
        rationale="default route",
    )


def _request(proposal_id: str, decision_id: str) -> OcExecutionRequest:
    return OcExecutionRequest(
        proposal_id=proposal_id,
        decision_id=decision_id,
        goal_text="Fix serializer null handling.",
        repo_key="svc",
        clone_url="https://github.com/ProtocolWarden/svc.git",
        base_branch="main",
        task_branch="auto/task-1",
        workspace_path=Path("/tmp/oc/ws"),
    )


def _result(request: OcExecutionRequest) -> OcExecutionResult:
    return OcExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.PASSED),
        artifacts=[
            ExecutionArtifact(
                artifact_type=ArtifactType.DIFF,
                label="diff",
                uri="file:///tmp/oc/ws/changes.diff",
            )
        ],
    )


def test_labels_are_projection_labels() -> None:
    assert TaskProposal is OcPlanningProposal
    assert LaneDecision is OcRoutingDecision
    assert ExecutionRequest is OcExecutionRequest
    assert ExecutionResult is OcExecutionResult


def test_proposal_boundary_serializes_to_canonical_cxrp_contract() -> None:
    cxrp = to_cxrp_task_proposal(_proposal())
    assert isinstance(cxrp, CxrpTaskProposal)
    assert not isinstance(cxrp, OcPlanningProposal)


def test_routing_boundary_serializes_to_and_from_canonical_cxrp_contract() -> None:
    internal = _decision("p-1")
    wire = to_cxrp_lane_decision(internal)
    assert isinstance(wire, CxrpLaneDecision)

    round_tripped = from_cxrp_lane_decision(wire.to_dict())
    assert isinstance(round_tripped, OcRoutingDecision)
    assert not isinstance(round_tripped, CxrpLaneDecision)


def test_execution_boundary_serializes_to_canonical_cxrp_contracts() -> None:
    request = _request("p-1", "d-1")
    result = _result(request)

    cxrp_request = to_cxrp_execution_request(
        request, executor=LaneName.CLAUDE_CLI.value, backend=BackendName.KODO.value
    )
    cxrp_result = to_cxrp_execution_result(result)

    assert isinstance(cxrp_request, CxrpExecutionRequest)
    assert isinstance(cxrp_result, CxrpExecutionResult)
    assert not isinstance(cxrp_request, OcExecutionRequest)
    assert not isinstance(cxrp_result, OcExecutionResult)


def test_docs_state_cxrp_owns_canonical_wire_semantics() -> None:
    root = _repo_root()
    readme = (root / "README.md").read_text(encoding="utf-8")
    contract_map = (root / "docs/architecture/contracts/contract-map.md").read_text(
        encoding="utf-8"
    )

    assert "CxRP owns the wire contracts" in readme
    assert "CxRP owns canonical cross-repo proposal, routing, and execution semantics." in contract_map
    assert "OcPlanningProposal" in contract_map
    assert "OcRoutingDecision" in contract_map
    assert "OcExecutionRequest" in contract_map
    assert "OcExecutionResult" in contract_map


def test_docs_do_not_describe_oc_internal_models_as_canonical_protocol_contracts() -> None:
    root = _repo_root()
    readme = (root / "README.md").read_text(encoding="utf-8")
    contract_map = (root / "docs/architecture/contracts/contract-map.md").read_text(
        encoding="utf-8"
    )

    assert "canonical `TaskProposal`" not in readme
    assert "canonical `LaneDecision`" not in readme
    assert "canonical `ExecutionRequest`" not in readme
    assert "canonical `ExecutionResult`" not in readme
    assert "| `TaskProposal` | `src/operations_center/contracts/proposal.py` |" not in contract_map
    assert "| `LaneDecision` | `src/operations_center/contracts/routing.py` |" not in contract_map
    assert "| `ExecutionRequest` | `src/operations_center/contracts/execution.py` |" not in contract_map
    assert "| `ExecutionResult` | `src/operations_center/contracts/execution.py` |" not in contract_map
