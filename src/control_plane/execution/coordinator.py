"""
execution/coordinator.py — supported canonical execution boundary.

This module makes the live supported execution path explicit:

    ProposalDecisionBundle
        -> ExecutionRequestBuilder
        -> PolicyEngine (mandatory)
        -> canonical adapter registry
        -> ExecutionResult
        -> ExecutionObservabilityService
"""

from __future__ import annotations

from dataclasses import dataclass

from control_plane.backends.factory import CanonicalBackendRegistry
from control_plane.contracts.common import ValidationSummary
from control_plane.contracts.enums import ExecutionStatus, FailureReasonCategory
from control_plane.contracts.enums import ValidationStatus
from control_plane.contracts.execution import ExecutionResult
from control_plane.observability.models import ExecutionRecord
from control_plane.observability.service import ExecutionObservabilityService
from control_plane.observability.trace import ExecutionTrace
from control_plane.planning.models import ProposalDecisionBundle
from control_plane.policy.engine import PolicyEngine
from control_plane.policy.models import PolicyDecision, PolicyStatus

from .handoff import ExecutionRequestBuilder, ExecutionRuntimeContext


@dataclass(frozen=True)
class ExecutionRunOutcome:
    request: object
    policy_decision: PolicyDecision
    result: ExecutionResult
    record: ExecutionRecord
    trace: ExecutionTrace
    executed: bool


class ExecutionCoordinator:
    """Supported execution boundary for canonical request handoff."""

    def __init__(
        self,
        *,
        adapter_registry: CanonicalBackendRegistry,
        policy_engine: PolicyEngine | None = None,
        request_builder: ExecutionRequestBuilder | None = None,
        observability_service: ExecutionObservabilityService | None = None,
    ) -> None:
        self._registry = adapter_registry
        self._policy = policy_engine or PolicyEngine.from_defaults()
        self._builder = request_builder or ExecutionRequestBuilder()
        self._observability = observability_service or ExecutionObservabilityService.default()

    def execute(
        self,
        bundle: ProposalDecisionBundle,
        runtime: ExecutionRuntimeContext,
    ) -> ExecutionRunOutcome:
        request = self._builder.build(bundle, runtime)
        policy_decision = self._policy.evaluate(bundle.proposal, bundle.decision, request)

        if policy_decision.status in {PolicyStatus.BLOCK, PolicyStatus.REQUIRE_REVIEW}:
            result = _policy_blocked_result(request, policy_decision)
            record, trace = self._observe(bundle, result, policy_decision)
            return ExecutionRunOutcome(
                request=request,
                policy_decision=policy_decision,
                result=result,
                record=record,
                trace=trace,
                executed=False,
            )

        request = self._builder.build(bundle, runtime, policy_decision)
        adapter = self._registry.for_backend(bundle.decision.selected_backend)
        result = adapter.execute(request)
        record, trace = self._observe(bundle, result, policy_decision)
        return ExecutionRunOutcome(
            request=request,
            policy_decision=policy_decision,
            result=result,
            record=record,
            trace=trace,
            executed=True,
        )

    def _observe(
        self,
        bundle: ProposalDecisionBundle,
        result: ExecutionResult,
        policy_decision: PolicyDecision,
    ) -> tuple[ExecutionRecord, ExecutionTrace]:
        return self._observability.observe(
            result,
            backend=bundle.decision.selected_backend.value,
            lane=bundle.decision.selected_lane.value,
            notes=policy_decision.notes,
            metadata={
                "policy": policy_decision.model_dump(mode="json"),
            },
        )


def _policy_blocked_result(request, policy_decision: PolicyDecision) -> ExecutionResult:
    reason = (
        "execution blocked by policy"
        if policy_decision.status == PolicyStatus.BLOCK
        else "execution requires review before autonomous execution"
    )
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.SKIPPED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=FailureReasonCategory.POLICY_BLOCKED,
        failure_reason=f"{reason}: {policy_decision.notes}",
    )
