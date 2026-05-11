# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Invariant tests for OC internal contract ownership vs CxRP wire ownership."""

from __future__ import annotations

from pathlib import Path

from cxrp.contracts import LaneDecision as CxrpLaneDecision
from cxrp.contracts import TaskProposal as CxrpTaskProposal
from operations_center.contracts.cxrp_mapper import (
    from_cxrp_lane_decision,
    to_cxrp_lane_decision,
    to_cxrp_task_proposal,
)
from operations_center.contracts.proposal import OcPlanningProposal, TaskProposal
from operations_center.contracts.routing import LaneDecision, OcRoutingDecision
from operations_center.contracts.common import BranchPolicy, ExecutionConstraints, TaskTarget, ValidationProfile
from operations_center.contracts.enums import BackendName, ExecutionMode, LaneName, Priority, RiskLevel, TaskType


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


def test_legacy_names_are_compatibility_aliases() -> None:
    assert TaskProposal is OcPlanningProposal
    assert LaneDecision is OcRoutingDecision


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


def test_docs_state_cxrp_owns_canonical_proposal_and_routing_semantics() -> None:
    root = _repo_root()
    readme = (root / "README.md").read_text(encoding="utf-8")
    contract_map = (root / "docs/architecture/contracts/contract-map.md").read_text(
        encoding="utf-8"
    )

    assert "CxRP owns the wire contracts" in readme
    assert "CxRP owns canonical cross-repo proposal and routing semantics." in contract_map
    assert "OcPlanningProposal" in contract_map
    assert "OcRoutingDecision" in contract_map


def test_docs_do_not_describe_oc_internal_models_as_canonical_protocol_contracts() -> None:
    root = _repo_root()
    readme = (root / "README.md").read_text(encoding="utf-8")
    contract_map = (root / "docs/architecture/contracts/contract-map.md").read_text(
        encoding="utf-8"
    )

    assert "canonical `TaskProposal`" not in readme
    assert "canonical `LaneDecision`" not in readme
    assert "| `TaskProposal` | `src/operations_center/contracts/proposal.py` |" not in contract_map
    assert "| `LaneDecision` | `src/operations_center/contracts/routing.py` |" not in contract_map
