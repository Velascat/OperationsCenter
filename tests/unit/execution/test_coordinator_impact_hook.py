# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Coordinator pre-dispatch contract-impact hook."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

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
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.policy.models import PolicyDecision, PolicyStatus
from operations_center.repo_graph_factory import build_effective_repo_graph


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


def _bundle(repo_key: str) -> ProposalDecisionBundle:
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


def _runtime() -> ExecutionRuntimeContext:
    return ExecutionRuntimeContext(
        workspace_path=Path("/tmp/workspace"),
        task_branch="auto/x",
    )


def _allow() -> _StubPolicy:
    return _StubPolicy(PolicyDecision(status=PolicyStatus.ALLOW))


class TestNoGraphSilent:
    def test_no_graph_no_log_no_metadata(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        bundle = _bundle("CxRP")
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
        )
        with caplog.at_level(logging.INFO):
            outcome = coord.execute(bundle, _runtime())
        assert all(
            "contract change" not in rec.message
            for rec in caplog.records
        )
        assert "contract_impact" not in (outcome.record.metadata or {})


class TestContractRepoImpactLogged:
    def test_cxrp_dispatch_logs_three_public_consumers(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        graph = build_effective_repo_graph()
        bundle = _bundle("CxRP")
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
            repo_graph=graph,
        )
        with caplog.at_level(logging.INFO):
            outcome = coord.execute(bundle, _runtime())

        impact_logs = [r for r in caplog.records if "contract change in CxRP" in r.message]
        assert len(impact_logs) == 1
        msg = impact_logs[0].message
        assert "OperationsCenter" in msg
        assert "SwitchBoard" in msg
        assert "OperatorConsole" in msg
        assert "[public=" in msg
        assert "private=" in msg

        meta = outcome.record.metadata or {}
        assert "contract_impact" in meta
        ci = meta["contract_impact"]
        assert ci["target"] == "CxRP"
        assert ci["target_repo_id"] == "cxrp"
        assert ci["affected_count"] >= 3
        assert {"OperationsCenter", "SwitchBoard", "OperatorConsole"}.issubset(
            set(ci["public_affected"])
        )

    def test_legacy_alias_resolves_in_repo_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        graph = build_effective_repo_graph()
        bundle = _bundle("ExecutionContractProtocol")
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
            repo_graph=graph,
        )
        with caplog.at_level(logging.INFO):
            outcome = coord.execute(bundle, _runtime())
        assert any(
            "contract change in CxRP" in rec.message for rec in caplog.records
        )
        assert outcome.record.metadata.get("contract_impact", {}).get("target") == "CxRP"


class TestLeafRepoSilent:
    def test_operator_console_is_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        graph = build_effective_repo_graph()
        bundle = _bundle("OperatorConsole")
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
            repo_graph=graph,
        )
        with caplog.at_level(logging.INFO):
            outcome = coord.execute(bundle, _runtime())
        assert all(
            "contract change" not in rec.message for rec in caplog.records
        )
        assert "contract_impact" not in (outcome.record.metadata or {})

    def test_unknown_repo_key_is_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        graph = build_effective_repo_graph()
        bundle = _bundle("velascat/unknown-service")
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
            repo_graph=graph,
        )
        with caplog.at_level(logging.INFO):
            outcome = coord.execute(bundle, _runtime())
        assert all(
            "contract change" not in rec.message for rec in caplog.records
        )
        assert "contract_impact" not in (outcome.record.metadata or {})


class TestPublicPrivatePartition:
    def test_private_consumer_surfaces_in_metadata(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        proj = tmp_path / "project.yaml"
        proj.write_text(
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  vfa_api:\n'
            '    canonical_name: VFAApi\n'
            '    visibility: private\n'
            'edges:\n'
            '  - {from: VFAApi, to: CxRP, type: depends_on_contracts_from}\n',
            encoding="utf-8",
        )
        graph = build_effective_repo_graph(project_manifest_path=proj)
        bundle = _bundle("CxRP")
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow(),
            repo_graph=graph,
        )
        with caplog.at_level(logging.INFO):
            outcome = coord.execute(bundle, _runtime())

        ci = outcome.record.metadata["contract_impact"]
        assert "VFAApi" in ci["private_affected"]
        assert {"OperationsCenter", "SwitchBoard", "OperatorConsole"}.issubset(
            set(ci["public_affected"])
        )
        assert ci["affected_count"] == len(ci["public_affected"]) + len(ci["private_affected"])
