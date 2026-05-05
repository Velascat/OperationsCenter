# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
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
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.observability.models import BackendDetailRef, ExecutionRecord
from operations_center.observability.service import ExecutionObservabilityService
from operations_center.observability.trace import ExecutionTrace
from operations_center.planning.models import ProposalDecisionBundle
from operations_center.policy.engine import PolicyEngine
from operations_center.policy.models import PolicyDecision, PolicyStatus

from .handoff import ExecutionRequestBuilder, ExecutionRuntimeContext
from .recovery_loop import (
    RecoveryAction,
    RecoveryContext,
    RecoveryDecision,
    RecoveryEngine,
    RecoveryPolicy,
    attach_recovery_metadata,
    bounded_sleep,
    build_default_engine,
)
from .workspace import WorkspaceManager

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
    request: ExecutionRequest
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
        workspace_manager: WorkspaceManager | None = None,
        recovery_engine: RecoveryEngine | None = None,
        recovery_policy: RecoveryPolicy | None = None,
    ) -> None:
        self._registry = adapter_registry
        self._policy = policy_engine or PolicyEngine.from_defaults()
        self._builder = request_builder or ExecutionRequestBuilder()
        self._observability = observability_service or ExecutionObservabilityService.default()
        self._workspace = workspace_manager
        # Recovery loop wiring. Defaults are conservative — max_attempts=1
        # means "no retry beyond the first attempt" so existing behavior is
        # preserved unless callers explicitly enable retry policy.
        self._recovery_policy = recovery_policy or RecoveryPolicy()
        self._recovery_engine = recovery_engine or build_default_engine(self._recovery_policy)

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

        # Pre-execution: clone the repo into the workspace and create the
        # task branch. Skipped when no WorkspaceManager is configured (unit
        # tests that mock adapters do not need a workspace).
        if self._workspace is not None:
            try:
                self._workspace.prepare(request)
            except Exception as exc:
                logger.error(
                    "Workspace prep failed for run %s: %s", request.run_id, exc,
                )
                result = _workspace_prep_failed_result(request, exc)
                record, trace = self._observe(bundle, result, policy_decision)
                return ExecutionRunOutcome(
                    request=request,
                    policy_decision=policy_decision,
                    result=result,
                    record=record,
                    trace=trace,
                    executed=False,
                )

        adapter = self._registry.for_backend(bundle.decision.selected_backend)
        result, raw_detail_refs, runtime_metadata, recovery_actions, policy_decision = (
            self._run_with_recovery_loop(
                adapter=adapter,
                bundle=bundle,
                runtime=runtime,
                request=request,
                policy_decision=policy_decision,
            )
        )
        if recovery_actions:
            result = attach_recovery_metadata(result, tuple(recovery_actions))

        # Post-execution: commit any pending changes, push the task branch,
        # optionally open a PR. Failures are logged but non-fatal — the
        # original adapter result still reports back.
        if self._workspace is not None and result.success:
            try:
                result = self._workspace.finalize(request, result)
            except Exception as exc:
                logger.warning(
                    "Workspace finalize failed for run %s: %s", request.run_id, exc,
                )

        record, trace = self._observe(
            bundle,
            result,
            policy_decision,
            raw_detail_refs=raw_detail_refs,
            runtime_metadata=runtime_metadata,
            request=request,
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
        request: ExecutionRequest | None = None,
    ) -> tuple[ExecutionRecord, ExecutionTrace]:
        metadata: dict[str, Any] = {
            "policy": policy_decision.model_dump(mode="json"),
            "task_type": bundle.proposal.task_type.value,
            "risk_level": bundle.proposal.risk_level.value,
        }
        if runtime_metadata:
            metadata.update(runtime_metadata)

        # Drift detection — only runs when the request carried a binding
        # AND the adapter reported what it observed via runtime_metadata.
        if request is not None and request.runtime_binding is not None:
            drift = _evaluate_runtime_drift(
                backend_id=bundle.decision.selected_backend.value,
                request=request,
                runtime_metadata=runtime_metadata or {},
            )
            if drift is not None:
                metadata["backend_drift"] = drift

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

    def _run_with_recovery_loop(
        self,
        *,
        adapter,
        bundle: ProposalDecisionBundle,
        runtime: ExecutionRuntimeContext,  # noqa: ARG002 — reserved for future request rebuild
        request: ExecutionRequest,
        policy_decision: PolicyDecision,
    ) -> tuple[
        ExecutionResult,
        list[BackendDetailRef],
        dict[str, Any],
        list[RecoveryAction],
        PolicyDecision,
    ]:
        """Bounded recovery loop wrapping ``_execute_adapter``.

        See ``docs/architecture/recovery_loop_design.md`` for the design.
        Per-call state is local to this method; the coordinator instance
        holds no mutable retry state, so concurrent ``execute()`` calls are
        safe.
        """
        current_request = request
        current_policy = policy_decision
        recovery_actions: list[RecoveryAction] = []
        last_result: ExecutionResult
        last_refs: list[BackendDetailRef] = []
        last_meta: dict[str, Any] = {}

        max_attempts = max(1, int(self._recovery_policy.max_attempts))
        for attempt in range(1, max_attempts + 1):
            last_result, last_refs, last_meta = self._execute_adapter(adapter, current_request)

            ctx = RecoveryContext(
                original_request=request,
                current_request=current_request,
                attempt=attempt,
                previous_actions=tuple(recovery_actions),
            )

            try:
                outcome = self._recovery_engine.evaluate(last_result, ctx)
            except Exception as exc:
                logger.exception(
                    "RecoveryEngine.evaluate raised for run %s: %s",
                    request.run_id, exc,
                )
                synthetic = RecoveryAction(
                    attempt=attempt,
                    failure_kind=__import__(
                        "operations_center.execution.recovery_loop",
                        fromlist=["ExecutionFailureKind"],
                    ).ExecutionFailureKind.UNKNOWN,
                    decision=RecoveryDecision.REJECT_UNRECOVERABLE,
                    reason=f"recovery engine raised: {exc!r}",
                    handler_name=None,
                )
                recovery_actions.append(synthetic)
                last_result = _recovery_engine_crash_result(current_request, exc)
                break

            recovery_actions.append(outcome.action)

            if outcome.decision == RecoveryDecision.ACCEPT:
                break
            if outcome.next_request is None:
                break

            # Bounded synchronous sleep before retry, if requested. The
            # bounded_sleep helper clamps to policy.max_delay_seconds.
            actual_delay: float | None = None
            if outcome.delay_seconds is not None:
                actual_delay = bounded_sleep(
                    outcome.delay_seconds,
                    self._recovery_policy.max_delay_seconds,
                )
                # Re-record the action with the actual slept duration.
                recovery_actions[-1] = RecoveryAction(
                    attempt=outcome.action.attempt,
                    failure_kind=outcome.action.failure_kind,
                    decision=outcome.action.decision,
                    reason=outcome.action.reason,
                    handler_name=outcome.action.handler_name,
                    modified_fields=outcome.action.modified_fields,
                    delay_seconds=actual_delay,
                )

            request_changed = outcome.next_request is not current_request
            current_request = outcome.next_request

            if request_changed or outcome.requires_policy_revalidation:
                # Modified request must be re-validated through PolicyEngine.
                # Defensive: if PolicyEngine raises, terminate the loop with
                # a synthetic crash result rather than propagating.
                try:
                    current_policy = self._policy.evaluate(
                        bundle.proposal, bundle.decision, current_request,
                    )
                except Exception as exc:
                    logger.exception(
                        "PolicyEngine.evaluate raised for run %s: %s",
                        request.run_id, exc,
                    )
                    last_result = _policy_engine_crash_result(current_request, exc)
                    break
                if current_policy.status in {PolicyStatus.BLOCK, PolicyStatus.REQUIRE_REVIEW}:
                    last_result = _policy_blocked_result(current_request, current_policy)
                    break

        return last_result, last_refs, last_meta, recovery_actions, current_policy

    def _build_detail_refs(self, adapter, request, capture) -> list[BackendDetailRef]:
        if capture is None:
            return []
        if isinstance(adapter, _DetailRefBuilder):
            try:
                return adapter.build_backend_detail_refs(request, capture)
            except Exception as exc:
                logger.warning("Failed to retain backend detail refs for run %s: %s", request.run_id, exc)
        return []


def _workspace_prep_failed_result(request, exc: Exception) -> ExecutionResult:
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason=f"Workspace preparation failed: {exc}",
    )


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


def _recovery_engine_crash_result(request, exc: Exception) -> ExecutionResult:
    """Synthetic result when ``RecoveryEngine.evaluate`` raises mid-loop.

    Defensive — recovery-layer exceptions must not propagate to the caller
    as uncategorized runtime errors.
    """
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason=f"RecoveryEngine raised unexpected exception: {exc}",
    )


def _policy_engine_crash_result(request, exc: Exception) -> ExecutionResult:
    """Synthetic result when ``PolicyEngine.evaluate`` raises mid-loop.

    Defensive — recovery-layer exceptions must not propagate to the caller
    as uncategorized runtime errors. Uses POLICY_BLOCKED so observers see
    this as a policy-side failure (which it effectively is).
    """
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=FailureReasonCategory.POLICY_BLOCKED,
        failure_reason=f"PolicyEngine.evaluate raised mid-loop: {exc}",
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
    metadata: dict[str, Any] = {}
    if duration_ms is not None:
        try:
            metadata["duration_ms"] = int(duration_ms)
        except (TypeError, ValueError):
            pass

    # Optional drift inputs — adapters that can report what they actually
    # ran can populate these on their capture for the drift detection layer.
    observed_runtime = getattr(capture, "observed_runtime", None)
    if isinstance(observed_runtime, dict):
        metadata["observed_runtime"] = dict(observed_runtime)

    used_capabilities = getattr(capture, "used_capabilities", None)
    if isinstance(used_capabilities, (list, tuple, set)):
        metadata["used_capabilities"] = sorted(str(c) for c in used_capabilities)

    return metadata


def _evaluate_runtime_drift(
    *,
    backend_id: str,
    request: ExecutionRequest,
    runtime_metadata: dict[str, Any],
) -> dict[str, Any] | None:
    """Compute BACKEND_DRIFT findings for a single execution.

    Returns a serializable dict (the drift payload) or None when there is
    no drift / the adapter didn't report observable fields.
    """
    rb = request.runtime_binding
    if rb is None:
        return None

    from operations_center.drift import detect_runtime_drift

    observed = runtime_metadata.get("observed_runtime") or {}
    bound = {
        k: v
        for k, v in (
            ("kind", rb.kind),
            ("model", rb.model),
            ("provider", rb.provider),
            ("endpoint", rb.endpoint),
        )
        if v is not None
    }
    finding = detect_runtime_drift(
        backend_id=backend_id,
        request_id=request.run_id,
        bound_runtime=bound,
        observed_runtime=observed,
    )
    return finding.to_dict() if finding is not None else None
