# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the supported canonical execution coordinator."""

from __future__ import annotations

from pathlib import Path

from operations_center.backends.kodo.runner import KodoAdapter
from operations_center.backends.kodo.adapter import KodoBackendAdapter
from operations_center.contracts.common import ChangedFileRef, ValidationSummary
from operations_center.contracts.enums import (
    BackendName,
    ExecutionStatus,
    FailureReasonCategory,
    LaneName,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionResult
from operations_center.observability.models import BackendDetailRef
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.execution.handoff import ExecutionRuntimeContext
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.policy.models import PolicyDecision, PolicyStatus


class _StubPolicyEngine:
    def __init__(self, decision: PolicyDecision) -> None:
        self._decision = decision
        self.called = 0

    def evaluate(self, proposal, decision, request=None) -> PolicyDecision:
        self.called += 1
        return self._decision


class _RecordingAdapter:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls = 0
        self.last_request = None

    def execute(self, request):
        self.calls += 1
        self.last_request = request
        return self.result


class _Registry:
    def __init__(self, adapter) -> None:
        self._adapter = adapter

    def for_backend(self, backend):
        return self._adapter


class _CaptureAdapter(_RecordingAdapter):
    def __init__(self, result: ExecutionResult, capture=None, refs=None) -> None:
        super().__init__(result)
        self.capture = capture if capture is not None else {"events": ["x"]}
        self.refs = refs if refs is not None else [
            BackendDetailRef(detail_type="event_trace", path="/tmp/raw-events.json")
        ]

    def execute_and_capture(self, request):
        self.calls += 1
        self.last_request = request
        return self.result, self.capture

    def build_backend_detail_refs(self, request, capture):
        assert capture is self.capture
        return list(self.refs)


def _bundle() -> ProposalDecisionBundle:
    proposal = build_proposal(
        PlanningContext(
            goal_text="Fix lint failures",
            task_type="lint_fix",
            repo_key="svc",
            clone_url="https://example.invalid/svc.git",
        )
    )
    return ProposalDecisionBundle(
        proposal=proposal,
        decision=_proposal_decision(proposal.proposal_id),
    )


def _proposal_decision(proposal_id: str):
    from operations_center.contracts.routing import LaneDecision

    return LaneDecision(
        proposal_id=proposal_id,
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=BackendName.DIRECT_LOCAL,
    )


def _runtime() -> ExecutionRuntimeContext:
    return ExecutionRuntimeContext(
        workspace_path=Path("/tmp/workspace"),
        task_branch="auto/lint-fix",
    )


def _success_result(bundle: ProposalDecisionBundle) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )


def test_policy_block_stops_before_adapter_invocation() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success_result(bundle))
    policy = _StubPolicyEngine(
        PolicyDecision(
            status=PolicyStatus.BLOCK,
            notes="blocked by repo policy",
        )
    )
    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=policy,
    )

    outcome = coordinator.execute(bundle, _runtime())

    assert policy.called == 1
    assert adapter.calls == 0
    assert outcome.executed is False
    assert outcome.result.failure_category.value == "policy_blocked"
    assert outcome.record.metadata["policy"]["status"] == "block"


def test_review_required_stops_before_adapter_invocation() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success_result(bundle))
    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_StubPolicyEngine(
            PolicyDecision(
                status=PolicyStatus.REQUIRE_REVIEW,
                notes="human review required",
            )
        ),
    )

    outcome = coordinator.execute(bundle, _runtime())

    assert adapter.calls == 0
    assert outcome.executed is False
    assert outcome.result.status == ExecutionStatus.SKIPPED


def test_allowed_policy_invokes_adapter_with_canonical_request() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success_result(bundle))
    policy = _StubPolicyEngine(
        PolicyDecision(
            status=PolicyStatus.ALLOW,
            effective_scope=["src/**"],
        )
    )
    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=policy,
    )

    outcome = coordinator.execute(bundle, _runtime())

    assert adapter.calls == 1
    assert adapter.last_request.proposal_id == bundle.proposal.proposal_id
    assert adapter.last_request.allowed_paths == ["src/**"]
    assert outcome.executed is True
    assert outcome.record.metadata["policy"]["status"] == "allow"
    assert outcome.record.metadata["task_type"] == "lint_fix"
    assert outcome.record.metadata["risk_level"] == "low"


def test_capture_capable_adapter_populates_backend_detail_refs() -> None:
    bundle = _bundle()
    adapter = _CaptureAdapter(_success_result(bundle))
    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
    )

    outcome = coordinator.execute(bundle, _runtime())

    assert outcome.executed is True
    assert adapter.calls == 1
    assert len(outcome.record.backend_detail_refs) == 1
    assert outcome.record.backend_detail_refs[0].detail_type == "event_trace"


def test_non_capture_adapter_keeps_backend_detail_refs_empty() -> None:
    bundle = _bundle()
    adapter = _RecordingAdapter(_success_result(bundle))
    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
    )

    outcome = coordinator.execute(bundle, _runtime())

    assert outcome.executed is True
    assert outcome.record.backend_detail_refs == []


def test_canonical_observability_preserves_inferred_changed_files_status() -> None:
    bundle = _bundle()
    result = ExecutionResult(
        run_id="run-2",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        changed_files=[ChangedFileRef(path="src/inferred.py", change_type="modified")],
        changed_files_source="event_stream",
        changed_files_confidence=0.5,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )
    adapter = _CaptureAdapter(result)
    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
    )

    outcome = coordinator.execute(bundle, _runtime())

    assert outcome.record.changed_files_evidence.status.value == "inferred"
    assert outcome.record.changed_files_evidence.source == "event_stream"
    assert outcome.record.changed_files_evidence.confidence == 0.5


def test_policy_blocked_run_has_no_backend_detail_refs() -> None:
    bundle = _bundle()
    adapter = _CaptureAdapter(_success_result(bundle))
    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_StubPolicyEngine(
            PolicyDecision(status=PolicyStatus.BLOCK, notes="blocked by policy")
        ),
    )

    outcome = coordinator.execute(bundle, _runtime())

    assert outcome.executed is False
    assert outcome.record.backend_detail_refs == []
    assert outcome.record.result.failure_category == FailureReasonCategory.POLICY_BLOCKED
    assert outcome.record.metadata["task_type"] == "lint_fix"
    assert outcome.record.metadata["risk_level"] == "low"
    assert "duration_ms" not in outcome.record.metadata


def test_capture_capable_adapter_persists_runtime_duration_metadata() -> None:
    bundle = _bundle()
    capture = type("Capture", (), {"duration_ms": 1234})()
    adapter = _CaptureAdapter(_success_result(bundle), capture=capture)
    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
    )

    outcome = coordinator.execute(bundle, _runtime())

    assert outcome.record.metadata["duration_ms"] == 1234


def test_kodo_adapter_retains_backend_detail_refs_and_duration_metadata(tmp_path: Path) -> None:
    bundle = _bundle()
    repo = tmp_path / "workspace"
    repo.mkdir()

    kodo = KodoAdapter.__new__(KodoAdapter)
    kodo.write_goal_file = lambda path, goal_text, constraints_text=None: path.write_text(goal_text, encoding="utf-8")
    kodo.build_command = lambda goal_file, repo_path, profile=None, kodo_mode="goal": ["kodo", "run"]

    # Phase 3: ExecutorRuntime drives the actual subprocess. Inject a
    # tiny fake that writes the desired stdout/stderr to the
    # invocation's artifact_directory and returns an RxP RuntimeResult.
    from datetime import datetime, timezone
    from rxp.contracts import RuntimeResult

    class _FakeRuntime:
        def run(self, invocation):
            ar = Path(invocation.artifact_directory) if invocation.artifact_directory else Path("/tmp")
            ar.mkdir(parents=True, exist_ok=True)
            sout = ar / "stdout.txt"
            sout.write_text("kodo stdout", encoding="utf-8")
            serr = ar / "stderr.txt"
            serr.write_text("", encoding="utf-8")
            now = datetime.now(timezone.utc).isoformat()
            return RuntimeResult(
                invocation_id=invocation.invocation_id,
                runtime_name=invocation.runtime_name,
                runtime_kind=invocation.runtime_kind,
                status="succeeded", exit_code=0,
                started_at=now, finished_at=now,
                stdout_path=str(sout), stderr_path=str(serr),
            )

    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(KodoBackendAdapter(kodo, runtime=_FakeRuntime())),
        policy_engine=_StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
    )

    outcome = coordinator.execute(
        bundle,
        ExecutionRuntimeContext(workspace_path=repo, task_branch="auto/lint-fix", goal_file_path=repo / ".goal.md"),
    )

    assert outcome.executed is True
    assert outcome.record.metadata["duration_ms"] >= 0
    assert outcome.record.metadata["task_type"] == "lint_fix"
    assert outcome.record.metadata["risk_level"] == "low"
    assert any(ref.detail_type == "stdout_log" for ref in outcome.record.backend_detail_refs)
    assert any(ref.detail_type == "structured_result" for ref in outcome.record.backend_detail_refs)
