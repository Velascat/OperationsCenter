"""Phase 3: OC's contracts produce wire payloads conforming to ECP v0.2.

Tests assert each OC→ECP mapper emits a shape that validates against the
canonical ECP JSON Schema, and that lane-specific payloads validate
against the named per-lane payload schema.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cxrp.contracts import (
    ExecutionResult as EcpExecutionResult,
    TaskProposal as EcpTaskProposal,
)
from cxrp.validation.json_schema import validate_contract, validate_payload
from cxrp.vocabulary.lane import LaneType

from operations_center.contracts.common import (
    BranchPolicy,
    ExecutionConstraints,
    TaskTarget,
    ValidationProfile,
    ValidationSummary,
)
from operations_center.contracts.ecp_mapper import (
    CODING_AGENT_INPUT_SCHEMA_ID,
    to_ecp_execution_request,
    to_ecp_execution_result,
    to_ecp_lane_decision,
    to_ecp_task_proposal,
)
from operations_center.contracts.enums import (
    ArtifactType,
    BackendName,
    ExecutionMode,
    ExecutionStatus,
    LaneName,
    Priority,
    RiskLevel,
    TaskType,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionArtifact, ExecutionRequest, ExecutionResult
from operations_center.contracts.proposal import TaskProposal
from operations_center.contracts.routing import LaneDecision


def _serialize_envelope(contract) -> dict:
    """Render an ECP dataclass tree to a wire-shaped dict.

    Mirrors BaseContract.to_dict() but recursively unwraps nested
    dataclasses and Enum values so the result is JSON-shape compatible.
    """
    payload = contract.to_dict()
    payload["lane"] = (
        payload["lane"].value if hasattr(payload["lane"], "value") else payload["lane"]
    )
    if "alternatives" in payload:
        payload["alternatives"] = [asdict(alt) for alt in contract.alternatives]
        for alt in payload["alternatives"]:
            alt["lane"] = (
                alt["lane"].value if hasattr(alt["lane"], "value") else alt["lane"]
            )
    return payload


def _serialize_result(result: EcpExecutionResult) -> dict:
    payload = result.to_dict()
    payload["status"] = (
        payload["status"].value if hasattr(payload["status"], "value") else payload["status"]
    )
    payload["artifacts"] = [asdict(a) for a in result.artifacts]
    return payload


def _make_target() -> TaskTarget:
    return TaskTarget(
        repo_key="velascat/api-service",
        clone_url="https://github.com/Velascat/api-service.git",
        base_branch="main",
    )


def _make_proposal() -> TaskProposal:
    return TaskProposal(
        task_id="task-001",
        project_id="proj-api",
        task_type=TaskType.BUG_FIX,
        execution_mode=ExecutionMode.GOAL,
        goal_text="Guard User.email access in UserSerializer; add a regression test.",
        constraints_text="do not modify migrations",
        target=_make_target(),
        priority=Priority.NORMAL,
        risk_level=RiskLevel.LOW,
        constraints=ExecutionConstraints(),
        validation_profile=ValidationProfile(profile_name="default"),
        branch_policy=BranchPolicy(),
        proposer="velascat",
        labels=["bug", "serializer"],
    )


def _make_decision(proposal_id: str) -> LaneDecision:
    return LaneDecision(
        proposal_id=proposal_id,
        selected_lane=LaneName.CLAUDE_CLI,
        selected_backend=BackendName.KODO,
        confidence=0.92,
        policy_rule_matched="bugfix-low-risk",
        rationale="task_type=bug_fix + risk=low → claude_cli via kodo",
        alternatives_considered=[LaneName.CODEX_CLI],
    )


def _make_request(proposal_id: str, decision_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        proposal_id=proposal_id,
        decision_id=decision_id,
        goal_text="Guard User.email access.",
        constraints_text="do not modify migrations",
        repo_key="velascat/api-service",
        clone_url="https://github.com/Velascat/api-service.git",
        base_branch="main",
        task_branch="fix/user-serializer-null-email",
        workspace_path=Path("/var/oc/workspaces/run-001"),
        goal_file_path=Path("/var/oc/workspaces/run-001/.goal.md"),
        allowed_paths=["src/**", "tests/**"],
        max_changed_files=25,
        timeout_seconds=600,
        require_clean_validation=True,
        validation_commands=["pytest -q"],
    )


def _make_result(request: ExecutionRequest) -> ExecutionResult:
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.PASSED, commands_run=1, commands_passed=1),
        branch_pushed=True,
        branch_name="fix/user-serializer-null-email",
        pull_request_url="https://github.com/Velascat/api-service/pull/482",
        artifacts=[
            ExecutionArtifact(
                artifact_type=ArtifactType.DIFF,
                label="patch produced by claude_cli",
                uri="file:///var/oc/workspaces/run-001/changes.diff",
                size_bytes=1024,
            ),
            ExecutionArtifact(
                artifact_type=ArtifactType.PR_URL,
                label="opened PR",
                uri="https://github.com/Velascat/api-service/pull/482",
            ),
        ],
        completed_at=datetime.now(tz=timezone.utc),
    )


# ----------------------------------------------------------------------
# TaskProposal
# ----------------------------------------------------------------------


def test_to_ecp_task_proposal_returns_ecp_envelope():
    ecp = to_ecp_task_proposal(_make_proposal())
    assert isinstance(ecp, EcpTaskProposal)
    assert ecp.contract_kind == "task_proposal"
    assert ecp.schema_version == "0.2"


def test_ecp_task_proposal_validates_against_schema():
    ecp = to_ecp_task_proposal(_make_proposal())
    validate_contract("task_proposal", ecp.to_dict())


def test_ecp_task_proposal_carries_layered_vocabulary():
    ecp = to_ecp_task_proposal(_make_proposal())
    assert ecp.task_type == "bug_fix"
    assert ecp.execution_mode == "goal"
    assert ecp.priority == "normal"
    assert ecp.risk_level == "low"


def test_ecp_task_proposal_target_uses_well_known_payload_schema():
    ecp = to_ecp_task_proposal(_make_proposal())
    target = ecp.target
    assert target is not None
    assert target["$payload_schema"] == "coding_agent_target/v0.2"
    assert target["repo_key"] == "velascat/api-service"


# ----------------------------------------------------------------------
# LaneDecision
# ----------------------------------------------------------------------


def test_to_ecp_lane_decision_separates_category_from_executor_backend():
    ecp = to_ecp_lane_decision(_make_decision("p-1"))
    assert ecp.lane == LaneType.CODING_AGENT
    assert ecp.executor == "claude_cli"
    assert ecp.backend == "kodo"


def test_ecp_lane_decision_validates_against_schema():
    ecp = to_ecp_lane_decision(_make_decision("p-1"))
    validate_contract("lane_decision", _serialize_envelope(ecp))


def test_ecp_lane_decision_alternatives_become_structured():
    ecp = to_ecp_lane_decision(_make_decision("p-1"))
    assert len(ecp.alternatives) == 1
    assert ecp.alternatives[0].executor == "codex_cli"


# ----------------------------------------------------------------------
# ExecutionRequest
# ----------------------------------------------------------------------


def test_to_ecp_execution_request_validates_against_schema():
    req = _make_request("p-1", "d-1")
    ecp = to_ecp_execution_request(req, executor="claude_cli", backend="kodo")
    payload = ecp.to_dict()
    payload["lane"] = payload["lane"].value if hasattr(payload["lane"], "value") else payload["lane"]
    validate_contract("execution_request", payload)


def test_execution_request_input_payload_validates_against_lane_schema():
    req = _make_request("p-1", "d-1")
    ecp = to_ecp_execution_request(req, executor="claude_cli", backend="kodo")
    assert ecp.input_payload_schema == CODING_AGENT_INPUT_SCHEMA_ID
    validate_payload(ecp.input_payload_schema, ecp.input_payload)


def test_execution_request_limits_are_universal():
    ecp = to_ecp_execution_request(_make_request("p", "d"), executor="claude_cli", backend="kodo")
    assert ecp.limits is not None
    assert ecp.limits.max_changed_files == 25
    assert ecp.limits.timeout_seconds == 600
    assert ecp.limits.require_clean_validation is True


# ----------------------------------------------------------------------
# ExecutionResult
# ----------------------------------------------------------------------


def test_to_ecp_execution_result_validates_against_schema():
    req = _make_request("p", "d")
    result = _make_result(req)
    ecp = to_ecp_execution_result(result)
    validate_contract("execution_result", _serialize_result(ecp))


def test_ecp_execution_result_status_uses_canonical_spelling():
    req = _make_request("p", "d")
    result = _make_result(req)
    ecp = to_ecp_execution_result(result)
    assert ecp.status.value == "succeeded"


def test_ecp_execution_result_artifact_kind_preserves_oc_vocabulary():
    req = _make_request("p", "d")
    result = _make_result(req)
    ecp = to_ecp_execution_result(result)
    kinds = {a.kind for a in ecp.artifacts}
    assert "diff" in kinds
    assert "pr_url" in kinds


def test_ecp_execution_result_diagnostics_carry_validation_summary():
    req = _make_request("p", "d")
    result = _make_result(req)
    ecp = to_ecp_execution_result(result)
    assert ecp.diagnostics["validation_status"] == "passed"
    assert ecp.diagnostics["branch_pushed"] is True


# ----------------------------------------------------------------------
# Boundary invariants
# ----------------------------------------------------------------------


def test_ecp_mapper_does_not_invoke_adapters_or_execution():
    """Boundary check: mapper module must be import-side-effect-free and
    must not pull in adapters/execution coordinators."""
    import operations_center.contracts.ecp_mapper as mod

    forbidden = (
        "operations_center.adapters",
        "operations_center.backends",
        "operations_center.execution.coordinator",
    )
    src = Path(mod.__file__).read_text()
    for needle in forbidden:
        assert needle not in src, f"ecp_mapper unexpectedly imports {needle}"


@pytest.mark.parametrize("status_value", ["succeeded", "failed", "cancelled", "timed_out"])
def test_ecp_status_round_trip_for_terminal_states(status_value):
    """All OC ExecutionStatus values that ECP also defines must round-trip."""
    req = _make_request("p", "d")
    oc = ExecutionResult(
        run_id=req.run_id,
        proposal_id=req.proposal_id,
        decision_id=req.decision_id,
        status=ExecutionStatus(status_value),
        success=(status_value == "succeeded"),
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )
    ecp = to_ecp_execution_result(oc)
    assert ecp.status.value == status_value
