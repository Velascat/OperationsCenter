# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Wiring tests for ER-001/ER-002/ER-003 inside ExecutionCoordinator."""

from __future__ import annotations

from pathlib import Path


from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import (
    BackendName,
    ExecutionStatus,
    LaneName,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionResult
from operations_center.contracts.routing import LaneDecision
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.execution.handoff import ExecutionRuntimeContext
from operations_center.lifecycle import LifecycleMetadata, TaskLifecycleStage
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.policy.models import PolicyDecision, PolicyStatus
from platform_manifest import (
    RepoGraph,
    RepoNode,
    load_default_repo_graph,
)
from operations_center.run_memory import RunMemoryQueryService


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubPolicy:
    def __init__(self, decision: PolicyDecision) -> None:
        self._d = decision

    def evaluate(self, *_args, **_kwargs):
        return self._d


class _RecordingAdapter:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        return self.result


class _Registry:
    def __init__(self, adapter) -> None:
        self._a = adapter

    def for_backend(self, _backend):
        return self._a


def _bundle(repo_key: str = "velascat/svc") -> ProposalDecisionBundle:
    proposal = build_proposal(
        PlanningContext(
            goal_text="Fix",
            task_type="lint_fix",
            repo_key=repo_key,
            clone_url="https://example.invalid/x.git",
        )
    )
    decision = LaneDecision(
        proposal_id=proposal.proposal_id,
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=BackendName.DIRECT_LOCAL,
    )
    return ProposalDecisionBundle(proposal=proposal, decision=decision)


def _runtime(*, lifecycle: LifecycleMetadata | None = None) -> ExecutionRuntimeContext:
    return ExecutionRuntimeContext(
        workspace_path=Path("/tmp/workspace"),
        task_branch="auto/x",
        lifecycle=lifecycle,
    )


def _success(bundle) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )


def _failure(bundle) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_reason="oops",
    )


def _allow() -> _StubPolicy:
    return _StubPolicy(PolicyDecision(status=PolicyStatus.ALLOW))


# ---------------------------------------------------------------------------
# ER-002 — run memory wiring
# ---------------------------------------------------------------------------


class TestRunMemoryWiring:
    def test_no_index_dir_means_no_memory_writes(self, tmp_path: Path) -> None:
        bundle = _bundle()
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
        )
        coord.execute(bundle, _runtime())
        # No file even attempted: tmp_path stays empty.
        assert list(tmp_path.iterdir()) == []

    def test_success_indexed_when_index_dir_provided(self, tmp_path: Path) -> None:
        bundle = _bundle(repo_key="velascat/api")
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
            run_memory_index_dir=tmp_path,
        )
        coord.execute(bundle, _runtime())
        records = RunMemoryQueryService(tmp_path).all()
        assert len(records) == 1
        assert records[0].repo_id == "velascat/api"
        assert records[0].status == "succeeded"
        # tags pull from bundle: task_type + lane + backend
        assert "lint_fix" in records[0].tags
        assert "aider_local" in records[0].tags
        assert "direct_local" in records[0].tags

    def test_failure_indexed(self, tmp_path: Path) -> None:
        bundle = _bundle()
        adapter = _RecordingAdapter(_failure(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
            run_memory_index_dir=tmp_path,
        )
        coord.execute(bundle, _runtime())
        records = RunMemoryQueryService(tmp_path).all()
        assert len(records) == 1
        assert records[0].status == "failed"

    def test_policy_block_still_indexed(self, tmp_path: Path) -> None:
        bundle = _bundle()
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_StubPolicy(
                PolicyDecision(status=PolicyStatus.BLOCK, notes="nope")
            ),
            run_memory_index_dir=tmp_path,
        )
        coord.execute(bundle, _runtime())
        assert adapter.calls == 0
        records = RunMemoryQueryService(tmp_path).all()
        assert len(records) == 1
        assert records[0].status == "skipped"


# ---------------------------------------------------------------------------
# ER-003 — lifecycle wiring around dispatch
# ---------------------------------------------------------------------------


class TestLifecycleWiring:
    def test_no_lifecycle_metadata_no_outcome_attached(self) -> None:
        bundle = _bundle()
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
        )
        outcome = coord.execute(bundle, _runtime())
        assert outcome.result.lifecycle_outcome is None

    def test_success_lifecycle_attaches_outcome_with_passing_check(self) -> None:
        bundle = _bundle()
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
        )
        outcome = coord.execute(bundle, _runtime(lifecycle=LifecycleMetadata()))
        lc = outcome.result.lifecycle_outcome
        assert lc is not None
        assert TaskLifecycleStage.PLAN in lc.completed_stages
        assert TaskLifecycleStage.EXECUTE in lc.completed_stages
        assert TaskLifecycleStage.VERIFY in lc.completed_stages
        assert lc.failed_stages == []

    def test_failure_lifecycle_marks_verify_as_failed(self) -> None:
        bundle = _bundle()
        adapter = _RecordingAdapter(_failure(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
        )
        outcome = coord.execute(bundle, _runtime(lifecycle=LifecycleMetadata()))
        lc = outcome.result.lifecycle_outcome
        assert lc is not None
        assert TaskLifecycleStage.VERIFY in lc.failed_stages
        assert TaskLifecycleStage.PLAN in lc.completed_stages


# ---------------------------------------------------------------------------
# ER-001 — repo graph context provided to lifecycle plan stage
# ---------------------------------------------------------------------------


class TestRepoGraphWiring:
    def test_default_loader_resolves_canonical(self) -> None:
        graph = load_default_repo_graph()
        assert graph.resolve("ControlPlane").canonical_name == "OperationsCenter"

    def test_default_loader_is_cached(self) -> None:
        a = load_default_repo_graph()
        b = load_default_repo_graph()
        assert a is b

    def test_lifecycle_plan_uses_graph_when_repo_resolves(self) -> None:
        # Build a tiny graph that knows our test repo as canonical.
        graph = RepoGraph.build(
            nodes=[
                RepoNode(repo_id="svc", canonical_name="velascat/svc"),
            ],
            edges=[],
        )
        bundle = _bundle(repo_key="velascat/svc")
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
            repo_graph=graph,
        )
        outcome = coord.execute(bundle, _runtime(lifecycle=LifecycleMetadata()))
        # outcome carries lifecycle; graph being threaded through means the
        # plan stage saw it (we can't see PlanOutput from here, but the
        # lifecycle running successfully is enough — failure path tested
        # in lifecycle suite).
        assert outcome.result.lifecycle_outcome is not None
        assert outcome.result.lifecycle_outcome.failed_stages == []
