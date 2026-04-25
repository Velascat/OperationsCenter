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
import logging
from typing import Any
from typing import Protocol, runtime_checkable

from operations_center.backends.factory import CanonicalBackendRegistry
from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.contracts.enums import ValidationStatus
from operations_center.contracts.execution import ExecutionResult
from operations_center.observability.models import BackendDetailRef, ExecutionRecord
from operations_center.observability.service import ExecutionObservabilityService
from operations_center.observability.trace import ExecutionTrace
from operations_center.planning.models import ProposalDecisionBundle
from operations_center.policy.engine import PolicyEngine
from operations_center.policy.models import PolicyDecision, PolicyStatus

from .handoff import ExecutionRequestBuilder, ExecutionRuntimeContext

logger = logging.getLogger(__name__)


@runtime_checkable
class _CaptureCapableAdapter(Protocol):
    def execute_and_capture(self, request) -> tuple[ExecutionResult, object | None]:
        ...


@runtime_checkable
class _DetailRefBuilder(Protocol):
    def build_backend_detail_refs(self, request, capture) -> list[BackendDetailRef]:
        ...


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
        result, raw_detail_refs, runtime_metadata = self._execute_adapter(adapter, request)
        record, trace = self._observe(
            bundle,
            result,
            policy_decision,
            raw_detail_refs=raw_detail_refs,
            runtime_metadata=runtime_metadata,
        )
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
        raw_detail_refs: list[BackendDetailRef] | None = None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> tuple[ExecutionRecord, ExecutionTrace]:
        metadata: dict[str, Any] = {
            "policy": policy_decision.model_dump(mode="json"),
            "task_type": bundle.proposal.task_type.value,
            "risk_level": bundle.proposal.risk_level.value,
        }
        if runtime_metadata:
            metadata.update(runtime_metadata)
        return self._observability.observe(
            result,
            backend=bundle.decision.selected_backend.value,
            lane=bundle.decision.selected_lane.value,
            raw_detail_refs=raw_detail_refs,
            notes=policy_decision.notes,
            metadata=metadata,
        )

    def _execute_adapter(self, adapter, request) -> tuple[ExecutionResult, list[BackendDetailRef], dict[str, Any]]:
        try:
            if isinstance(adapter, _CaptureCapableAdapter):
                result, capture = adapter.execute_and_capture(request)
                refs = self._build_detail_refs(adapter, request, capture)
                return result, refs, _runtime_metadata_from_capture(capture)
            return adapter.execute(request), [], {}
        except Exception as exc:
            logger.error("Adapter raised unexpected exception for run %s: %s", request.run_id, exc)
            return _adapter_crash_result(request, exc), [], {}

    def _build_detail_refs(self, adapter, request, capture) -> list[BackendDetailRef]:
        if capture is None:
            return []
        if isinstance(adapter, _DetailRefBuilder):
            try:
                return adapter.build_backend_detail_refs(request, capture)
            except Exception as exc:
                logger.warning("Failed to retain backend detail refs for run %s: %s", request.run_id, exc)
        return []


def _adapter_crash_result(request, exc: Exception) -> ExecutionResult:
    from operations_center.contracts.common import ValidationSummary
    from operations_center.contracts.enums import ValidationStatus
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason=f"Adapter raised unexpected exception: {exc}",
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


def _runtime_metadata_from_capture(capture: object | None) -> dict[str, Any]:
    if capture is None:
        return {}

    duration_ms = getattr(capture, "duration_ms", None)
    if duration_ms is None:
        return {}

    try:
        return {"duration_ms": int(duration_ms)}
    except (TypeError, ValueError):
        return {}
