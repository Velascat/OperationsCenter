"""Tests for RunArtifactWriter."""

from __future__ import annotations

import json
from pathlib import Path


from operations_center.contracts.enums import BackendName, LaneName, ExecutionStatus, FailureReasonCategory
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.contracts.routing import LaneDecision
from operations_center.execution.artifact_writer import RunArtifactWriter
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.execution.handoff import ExecutionRequestBuilder, ExecutionRuntimeContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bundle() -> ProposalDecisionBundle:
    proposal = build_proposal(
        PlanningContext(
            goal_text="Fix lint errors",
            task_type="lint_fix",
            repo_key="svc",
            clone_url="https://example.invalid/svc.git",
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


def _request(bundle: ProposalDecisionBundle, tmp_path: Path) -> ExecutionRequest:
    return ExecutionRequestBuilder().build(
        bundle,
        ExecutionRuntimeContext(workspace_path=tmp_path / "ws", task_branch="auto/test"),
    )


def _success_result(request: ExecutionRequest) -> ExecutionResult:
    from operations_center.contracts.common import ValidationSummary
    from operations_center.contracts.enums import ValidationStatus
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.SUCCESS,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.PASSED),
    )


def _failure_result(request: ExecutionRequest) -> ExecutionResult:
    from operations_center.contracts.common import ValidationSummary
    from operations_center.contracts.enums import ValidationStatus
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason="binary not found",
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )


# ---------------------------------------------------------------------------
# write_run — file creation
# ---------------------------------------------------------------------------


class TestWriteRun:
    def test_creates_run_directory(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=request,
            result=result,
            executed=True,
        )
        run_dir = tmp_path / "runs" / result.run_id
        assert run_dir.is_dir()

    def test_writes_all_five_files(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        writer = RunArtifactWriter(root=tmp_path / "runs")
        written = writer.write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=request,
            result=result,
            executed=True,
        )
        assert len(written) == 5
        run_dir = tmp_path / "runs" / result.run_id
        for name in ("proposal.json", "decision.json", "execution_request.json", "result.json", "run_metadata.json"):
            assert (run_dir / name).exists(), f"{name} missing"

    def test_returns_paths_as_strings(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        writer = RunArtifactWriter(root=tmp_path / "runs")
        written = writer.write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=request,
            result=result,
            executed=True,
        )
        assert all(isinstance(p, str) for p in written)

    def test_proposal_json_is_valid(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=request,
            result=result,
            executed=True,
        )
        run_dir = tmp_path / "runs" / result.run_id
        data = json.loads((run_dir / "proposal.json").read_text())
        assert data["proposal_id"] == bundle.proposal.proposal_id

    def test_decision_json_is_valid(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=request,
            result=result,
            executed=True,
        )
        run_dir = tmp_path / "runs" / result.run_id
        data = json.loads((run_dir / "decision.json").read_text())
        assert data["decision_id"] == bundle.decision.decision_id
        assert data["selected_lane"] == LaneName.AIDER_LOCAL.value

    def test_result_json_is_valid(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=request,
            result=result,
            executed=True,
        )
        run_dir = tmp_path / "runs" / result.run_id
        data = json.loads((run_dir / "result.json").read_text())
        assert data["success"] is True
        assert data["status"] == ExecutionStatus.SUCCESS.value

    def test_execution_request_json_is_valid(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=request,
            result=result,
            executed=True,
        )
        run_dir = tmp_path / "runs" / result.run_id
        data = json.loads((run_dir / "execution_request.json").read_text())
        assert data["proposal_id"] == bundle.proposal.proposal_id
        assert data["goal_text"] == "Fix lint errors"


# ---------------------------------------------------------------------------
# write_run — run_metadata.json
# ---------------------------------------------------------------------------


class TestRunMetadata:
    def _write(self, tmp_path, result, executed=True):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=request,
            result=result,
            executed=executed,
        )
        run_dir = tmp_path / "runs" / result.run_id
        return json.loads((run_dir / "run_metadata.json").read_text()), bundle

    def test_metadata_contains_run_id(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        metadata, _ = self._write(tmp_path, result)
        assert metadata["run_id"] == result.run_id

    def test_metadata_contains_lane_and_backend(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        metadata, _ = self._write(tmp_path, result)
        assert metadata["selected_lane"] == LaneName.AIDER_LOCAL.value
        assert metadata["selected_backend"] == BackendName.DIRECT_LOCAL.value

    def test_metadata_success_status(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        metadata, _ = self._write(tmp_path, result)
        assert metadata["success"] is True
        assert metadata["executed"] is True

    def test_metadata_failure_category_included(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _failure_result(request)
        metadata, _ = self._write(tmp_path, result)
        assert metadata["failure_category"] == FailureReasonCategory.BACKEND_ERROR.value

    def test_metadata_no_failure_category_on_success(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        metadata, _ = self._write(tmp_path, result)
        assert "failure_category" not in metadata

    def test_metadata_written_at_present(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        metadata, _ = self._write(tmp_path, result)
        assert "written_at" in metadata
        assert metadata["written_at"].endswith("+00:00")

    def test_extra_metadata_merged(self, tmp_path):
        bundle = _bundle()
        request = _request(bundle, tmp_path)
        result = _success_result(request)
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=request,
            result=result,
            executed=True,
            extra_metadata={"custom_key": "custom_value"},
        )
        run_dir = tmp_path / "runs" / result.run_id
        metadata = json.loads((run_dir / "run_metadata.json").read_text())
        assert metadata["custom_key"] == "custom_value"


# ---------------------------------------------------------------------------
# write_partial
# ---------------------------------------------------------------------------


class TestWritePartial:
    def test_creates_run_directory(self, tmp_path):
        bundle = _bundle()
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_partial(run_id="partial-run-1", proposal=bundle.proposal, reason="SwitchBoard down")
        assert (tmp_path / "runs" / "partial-run-1").is_dir()

    def test_writes_proposal_when_provided(self, tmp_path):
        bundle = _bundle()
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_partial(run_id="partial-run-1", proposal=bundle.proposal)
        assert (tmp_path / "runs" / "partial-run-1" / "proposal.json").exists()

    def test_does_not_write_proposal_when_absent(self, tmp_path):
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_partial(run_id="partial-run-1")
        assert not (tmp_path / "runs" / "partial-run-1" / "proposal.json").exists()

    def test_writes_decision_when_provided(self, tmp_path):
        bundle = _bundle()
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_partial(run_id="partial-run-1", decision=bundle.decision)
        assert (tmp_path / "runs" / "partial-run-1" / "decision.json").exists()

    def test_metadata_marks_partial(self, tmp_path):
        writer = RunArtifactWriter(root=tmp_path / "runs")
        writer.write_partial(run_id="partial-run-1", reason="SwitchBoard down")
        metadata = json.loads((tmp_path / "runs" / "partial-run-1" / "run_metadata.json").read_text())
        assert metadata["partial"] is True
        assert metadata["reason"] == "SwitchBoard down"
        assert metadata["run_id"] == "partial-run-1"

    def test_returns_written_paths(self, tmp_path):
        bundle = _bundle()
        writer = RunArtifactWriter(root=tmp_path / "runs")
        written = writer.write_partial(run_id="partial-run-1", proposal=bundle.proposal)
        assert any("proposal.json" in p for p in written)
        assert any("run_metadata.json" in p for p in written)
