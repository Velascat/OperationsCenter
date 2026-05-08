# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Round-3 wiring: build_effective_repo_graph + ExecutionCoordinator.

Confirms OC consumes the merged EffectiveRepoGraph (platform + project
+ local) rather than a bare platform manifest, and that the lifecycle
plan stage still resolves repo identity through it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from platform_manifest import Source, Visibility

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
from operations_center.lifecycle import LifecycleMetadata
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.policy.models import PolicyDecision, PolicyStatus
from operations_center.repo_graph_factory import build_effective_repo_graph


# ---------------------------------------------------------------------------
# Helpers
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


def _bundle(repo_key: str = "OperationsCenter") -> ProposalDecisionBundle:
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


def _success(bundle) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )


def _allow() -> _StubPolicy:
    return _StubPolicy(PolicyDecision(status=PolicyStatus.ALLOW))


# ---------------------------------------------------------------------------
# build_effective_repo_graph
# ---------------------------------------------------------------------------


class TestPlatformOnly:
    def test_default_base_loads(self) -> None:
        g = build_effective_repo_graph()
        # Same set as the bundled YAML — every node is platform-sourced.
        assert g.resolve("OperationsCenter") is not None
        for node in g.list_nodes():
            assert node.source is Source.PLATFORM
            assert node.visibility is Visibility.PUBLIC


class TestThreeLayerComposition:
    def test_project_and_local_layered_in(self, tmp_path: Path) -> None:
        proj = tmp_path / "project.yaml"
        proj.write_text(
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  example_api:\n'
            '    canonical_name: ExampleAPI\n'
            '    visibility: private\n'
            '    runtime_role: project_service\n'
            'edges:\n'
            '  - {from: ExampleAPI, to: OperationsCenter, type: dispatches_to}\n',
            encoding="utf-8",
        )
        local = tmp_path / "local.yaml"
        local.write_text(
            'manifest_kind: local\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  operations_center:\n'
            '    local_path: /home/dev/src/OperationsCenter\n'
            '    local_port: 8080\n'
            '  example_api:\n'
            '    local_path: /home/dev/private/example_api\n'
            '    gpu_required: true\n',
            encoding="utf-8",
        )
        g = build_effective_repo_graph(
            project_manifest_path=proj,
            local_manifest_path=local,
        )

        # Project node is in
        api = g.resolve("ExampleAPI")
        assert api is not None
        assert api.source is Source.PROJECT
        assert api.visibility is Visibility.PRIVATE

        # Local annotated platform node
        oc = g.resolve("OperationsCenter")
        assert oc.local_path == "/home/dev/src/OperationsCenter"
        assert oc.local_port == 8080
        assert oc.source is Source.PLATFORM  # local doesn't change provenance

        # Local annotated project node
        api = g.resolve("ExampleAPI")
        assert api.local_path == "/home/dev/private/example_api"
        assert api.gpu_required is True

        # Project edge into platform survives
        oc_inbound = {e.src for e in g.edges if e.dst == "operations_center"}
        assert "example_api" in oc_inbound


# ---------------------------------------------------------------------------
# Coordinator wiring with composed graph
# ---------------------------------------------------------------------------


class TestCoordinatorReceivesEffectiveGraph:
    def test_lifecycle_plan_resolves_repo_through_effective_graph(self, tmp_path: Path) -> None:
        # Build a composed graph that includes a project repo and use it
        # as the lifecycle plan-stage repo identity context.
        proj = tmp_path / "project.yaml"
        proj.write_text(
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  example_api:\n'
            '    canonical_name: ExampleAPI\n'
            '    visibility: private\n',
            encoding="utf-8",
        )
        graph = build_effective_repo_graph(project_manifest_path=proj)

        bundle = _bundle(repo_key="ExampleAPI")
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
            repo_graph=graph,
        )
        runtime = ExecutionRuntimeContext(
            workspace_path=Path("/tmp/workspace"),
            task_branch="auto/x",
            lifecycle=LifecycleMetadata(),
        )
        outcome = coord.execute(bundle, runtime)
        # Lifecycle ran (plan resolved repo identity through the effective graph).
        assert outcome.result.lifecycle_outcome is not None
        assert adapter.calls == 1


# ---------------------------------------------------------------------------
# Failure-case smoke through the OC helper
# ---------------------------------------------------------------------------


class TestComposeFailuresPropagate:
    def test_project_redefining_platform_repo_propagates(self, tmp_path: Path) -> None:
        proj = tmp_path / "project.yaml"
        proj.write_text(
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  operations_center:\n'
            '    canonical_name: MyCustomOperationsCenter\n'
            '    visibility: private\n',
            encoding="utf-8",
        )
        # PM raises RepoGraphConfigError; we pass it through unchanged.
        from platform_manifest import RepoGraphConfigError

        with pytest.raises(RepoGraphConfigError, match="cannot redefine"):
            build_effective_repo_graph(project_manifest_path=proj)
