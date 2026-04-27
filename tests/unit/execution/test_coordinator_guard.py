"""Tests for ExecutionCoordinator's adapter crash guard.

The coordinator must return a structured ExecutionResult when an adapter
raises an unexpected exception, rather than propagating the exception.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


from operations_center.backends.factory import CanonicalBackendRegistry
from operations_center.contracts.enums import (
    BackendName,
    ExecutionStatus,
    FailureReasonCategory,
    LaneName,
)
from operations_center.contracts.execution import ExecutionResult
from operations_center.contracts.routing import LaneDecision
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.execution.handoff import ExecutionRuntimeContext
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal


# ---------------------------------------------------------------------------
# Helpers
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


def _crashing_adapter(exc: Exception):
    class _Crash:
        def execute(self, request):
            raise exc
    return _Crash()


def _registry_for(adapter) -> CanonicalBackendRegistry:
    registry = MagicMock(spec=CanonicalBackendRegistry)
    registry.for_backend.return_value = adapter
    return registry


def _runtime(tmp_path: Path) -> ExecutionRuntimeContext:
    return ExecutionRuntimeContext(
        workspace_path=tmp_path / "ws",
        task_branch="auto/test",
    )


# ---------------------------------------------------------------------------
# Adapter crash guard
# ---------------------------------------------------------------------------


class TestAdapterCrashGuard:
    def test_runtime_error_returns_failure_result(self, tmp_path):
        bundle = _bundle()
        registry = _registry_for(_crashing_adapter(RuntimeError("something broke")))
        coordinator = ExecutionCoordinator(adapter_registry=registry)
        outcome = coordinator.execute(bundle, _runtime(tmp_path))
        assert isinstance(outcome.result, ExecutionResult)
        assert outcome.result.success is False

    def test_runtime_error_sets_failed_status(self, tmp_path):
        bundle = _bundle()
        registry = _registry_for(_crashing_adapter(RuntimeError("boom")))
        coordinator = ExecutionCoordinator(adapter_registry=registry)
        outcome = coordinator.execute(bundle, _runtime(tmp_path))
        assert outcome.result.status == ExecutionStatus.FAILED

    def test_runtime_error_sets_backend_error_category(self, tmp_path):
        bundle = _bundle()
        registry = _registry_for(_crashing_adapter(RuntimeError("boom")))
        coordinator = ExecutionCoordinator(adapter_registry=registry)
        outcome = coordinator.execute(bundle, _runtime(tmp_path))
        assert outcome.result.failure_category == FailureReasonCategory.BACKEND_ERROR

    def test_failure_reason_contains_exception_message(self, tmp_path):
        bundle = _bundle()
        registry = _registry_for(_crashing_adapter(RuntimeError("disk full")))
        coordinator = ExecutionCoordinator(adapter_registry=registry)
        outcome = coordinator.execute(bundle, _runtime(tmp_path))
        assert "disk full" in (outcome.result.failure_reason or "")

    def test_run_id_preserved_on_crash(self, tmp_path):
        bundle = _bundle()
        registry = _registry_for(_crashing_adapter(RuntimeError("boom")))
        coordinator = ExecutionCoordinator(adapter_registry=registry)
        outcome = coordinator.execute(bundle, _runtime(tmp_path))
        assert outcome.result.run_id  # non-empty

    def test_proposal_id_preserved_on_crash(self, tmp_path):
        bundle = _bundle()
        registry = _registry_for(_crashing_adapter(RuntimeError("boom")))
        coordinator = ExecutionCoordinator(adapter_registry=registry)
        outcome = coordinator.execute(bundle, _runtime(tmp_path))
        assert outcome.result.proposal_id == bundle.proposal.proposal_id

    def test_decision_id_preserved_on_crash(self, tmp_path):
        bundle = _bundle()
        registry = _registry_for(_crashing_adapter(RuntimeError("boom")))
        coordinator = ExecutionCoordinator(adapter_registry=registry)
        outcome = coordinator.execute(bundle, _runtime(tmp_path))
        assert outcome.result.decision_id == bundle.decision.decision_id

    def test_executed_is_true_even_on_crash(self, tmp_path):
        # The adapter was invoked — execution was attempted even if it crashed
        bundle = _bundle()
        registry = _registry_for(_crashing_adapter(RuntimeError("boom")))
        coordinator = ExecutionCoordinator(adapter_registry=registry)
        outcome = coordinator.execute(bundle, _runtime(tmp_path))
        assert outcome.executed is True

    def test_result_is_json_serialisable(self, tmp_path):
        import json as _json
        bundle = _bundle()
        registry = _registry_for(_crashing_adapter(ValueError("bad value")))
        coordinator = ExecutionCoordinator(adapter_registry=registry)
        outcome = coordinator.execute(bundle, _runtime(tmp_path))
        payload = _json.loads(outcome.result.model_dump_json())
        assert payload["success"] is False

    def test_os_error_handled(self, tmp_path):
        bundle = _bundle()
        registry = _registry_for(_crashing_adapter(OSError("permission denied")))
        coordinator = ExecutionCoordinator(adapter_registry=registry)
        outcome = coordinator.execute(bundle, _runtime(tmp_path))
        assert outcome.result.success is False
        assert "permission denied" in (outcome.result.failure_reason or "")


# ---------------------------------------------------------------------------
# ROUTING_ERROR category exists
# ---------------------------------------------------------------------------


def test_routing_error_category_exists():
    assert FailureReasonCategory.ROUTING_ERROR.value == "routing_error"
