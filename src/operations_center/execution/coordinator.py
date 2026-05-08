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
from pathlib import Path
from typing import TYPE_CHECKING, Any
from typing import Protocol, runtime_checkable

if TYPE_CHECKING:
    from operations_center.policy.runtime_binding_policy import RuntimeBindingPolicy

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

from datetime import UTC, datetime

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
from .usage_store import UsageStore
from .workspace import WorkspaceManager

if TYPE_CHECKING:
    from operations_center.config.settings import (
        BackendCapSettings,
        ResourceGateSettings,
    )
    from operations_center.execution.models import BudgetDecision

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
        runtime_binding_policy: "RuntimeBindingPolicy | None" = None,
        run_memory_index_dir: Path | None = None,
        repo_graph: object | None = None,
        usage_store: UsageStore | None = None,
        backend_caps: dict[str, "BackendCapSettings"] | None = None,
        resource_gate: "ResourceGateSettings | None" = None,
    ) -> None:
        self._registry = adapter_registry
        self._policy = policy_engine or PolicyEngine.from_defaults()
        self._builder = request_builder or ExecutionRequestBuilder()
        self._observability = observability_service or ExecutionObservabilityService.default()
        self._workspace = workspace_manager
        # Option B — task-shape → model selection at request build time.
        # When the caller doesn't supply a policy, we leave runtime_binding
        # selection off (passthrough — adapters use their built-in defaults).
        # The bundled DEFAULT_POLICY is opt-in via from_defaults_with_runtime_policy().
        self._runtime_binding_policy = runtime_binding_policy
        # Recovery loop wiring. Defaults are conservative — max_attempts=1
        # means "no retry beyond the first attempt" so existing behavior is
        # preserved unless callers explicitly enable retry policy.
        self._recovery_policy = recovery_policy or RecoveryPolicy()
        self._recovery_engine = recovery_engine or build_default_engine(self._recovery_policy)
        # ER-002 — opt-in run memory index. None preserves existing behavior.
        self._run_memory_index_dir = run_memory_index_dir
        # ER-001 — optional repo graph context handed to lifecycle plan stage.
        self._repo_graph = repo_graph
        # Per-backend rate / concurrency / RAM enforcement. Both default to
        # None so existing tests (which construct the coordinator with stub
        # adapters) keep passing unchanged. When ``usage_store`` is supplied
        # but ``backend_caps`` is empty, dispatches still record started/
        # finished + execution events for observability — the rate-cap
        # check itself is a no-op for backends without an entry in the map.
        self._usage_store = usage_store
        self._backend_caps: dict[str, "BackendCapSettings"] = dict(backend_caps or {})
        # Global resource gate — runs before per-backend caps and reserves
        # host headroom for co-tenant workloads sharing the box.
        self._resource_gate: "ResourceGateSettings | None" = resource_gate

    def execute(
        self,
        bundle: ProposalDecisionBundle,
        runtime: ExecutionRuntimeContext,
    ) -> ExecutionRunOutcome:
        runtime = self._apply_runtime_binding_policy(bundle, runtime)
        request = self._builder.build(bundle, runtime)
        policy_decision = self._policy.evaluate(bundle.proposal, bundle.decision, request)

        if policy_decision.status in {PolicyStatus.BLOCK, PolicyStatus.REQUIRE_REVIEW}:
            result = _policy_blocked_result(request, policy_decision)
            record, trace = self._observe(bundle, result, policy_decision)
            self._record_run_memory(request=request, result=result, bundle=bundle)
            return ExecutionRunOutcome(
                request=request,
                policy_decision=policy_decision,
                result=result,
                record=record,
                trace=trace,
                executed=False,
            )

        request = self._builder.build(bundle, runtime, policy_decision)

        # Pre-dispatch contract impact analysis. Logs a structured INFO
        # line and returns a metadata dict (empty when no graph or no
        # impact) that we merge into the observability record below.
        pre_dispatch_metadata = self._log_contract_impact(request)

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
                self._record_run_memory(request=request, result=result, bundle=bundle)
                return ExecutionRunOutcome(
                    request=request,
                    policy_decision=policy_decision,
                    result=result,
                    record=record,
                    trace=trace,
                    executed=False,
                )

        backend_name = bundle.decision.selected_backend.value

        # Global resource gate — evaluated before per-backend caps so a
        # mix of small dispatches across many backends still can't drain
        # the headroom reserved for co-tenant workloads on the same host.
        gate_decision = self._evaluate_resource_gate()
        if gate_decision is not None and not gate_decision.allowed:
            result = _resource_gate_blocked_result(request, gate_decision)
            record, trace = self._observe(bundle, result, policy_decision)
            self._record_run_memory(request=request, result=result, bundle=bundle)
            return ExecutionRunOutcome(
                request=request,
                policy_decision=policy_decision,
                result=result,
                record=record,
                trace=trace,
                executed=False,
            )

        # Pre-dispatch backend cap enforcement (rate / concurrency / RAM).
        # Returns (decision, allowed). When not allowed we surface a
        # canonical "skipped due to backend cap" result and don't dispatch.
        cap_decision = self._evaluate_backend_caps(backend_name)
        if cap_decision is not None and not cap_decision.allowed:
            result = _backend_capped_result(request, cap_decision, backend_name)
            record, trace = self._observe(bundle, result, policy_decision)
            self._record_run_memory(request=request, result=result, bundle=bundle)
            return ExecutionRunOutcome(
                request=request,
                policy_decision=policy_decision,
                result=result,
                record=record,
                trace=trace,
                executed=False,
            )

        adapter = self._registry.for_backend(bundle.decision.selected_backend)

        # Mark the dispatch in flight for concurrency accounting. The
        # finished marker fires from a finally block so a crashed adapter
        # can't deadlock the per-backend max_concurrent cap.
        if self._usage_store is not None:
            self._usage_store.record_execution_started(
                task_id=request.run_id, backend=backend_name,
                now=datetime.now(UTC),
            )
        try:
            result, raw_detail_refs, runtime_metadata, recovery_actions, policy_decision = (
                self._run_with_recovery_loop(
                    adapter=adapter,
                    bundle=bundle,
                    runtime=runtime,
                    request=request,
                    policy_decision=policy_decision,
                )
            )
        finally:
            if self._usage_store is not None:
                self._usage_store.record_execution_finished(
                    task_id=request.run_id, backend=backend_name,
                    now=datetime.now(UTC),
                )
        if recovery_actions:
            result = attach_recovery_metadata(result, tuple(recovery_actions))

        # Tag the dispatch for downstream rate caps + circuit breaker.
        # Recorded once per coordinator.execute() — the recovery loop's
        # internal retries don't double-count.
        if self._usage_store is not None:
            now = datetime.now(UTC)
            role = bundle.proposal.task_type.value
            self._usage_store.record_execution(
                role=role,
                task_id=request.run_id,
                signature=request.run_id,  # one-shot signature; coordinator never reuses run_id
                now=now,
                repo_key=request.repo_key,
                backend=backend_name,
            )
            self._usage_store.record_execution_outcome(
                task_id=request.run_id,
                role=role,
                succeeded=result.success,
                now=now,
                backend=backend_name,
            )

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

        # ER-003 — if the request carried lifecycle metadata, drive
        # plan/verify around the dispatch we just ran. The dispatch result
        # itself remains the canonical execution; lifecycle augments it.
        if request.lifecycle is not None:
            result = _attach_lifecycle_outcome(
                request=request,
                result=result,
                repo_graph_context=self._repo_graph,
            )

        merged_metadata = {**pre_dispatch_metadata, **(runtime_metadata or {})}
        record, trace = self._observe(
            bundle,
            result,
            policy_decision,
            raw_detail_refs=raw_detail_refs,
            runtime_metadata=merged_metadata,
            request=request,
        )
        # ER-002 — index the finalized result. Single write site for
        # OperationsCenter. Failures are swallowed: memory is advisory.
        self._record_run_memory(request=request, result=result, bundle=bundle)
        return ExecutionRunOutcome(
            request=request,
            policy_decision=policy_decision,
            result=result,
            record=record,
            trace=trace,
            executed=True,
        )

    def _apply_runtime_binding_policy(
        self,
        bundle: ProposalDecisionBundle,
        runtime: ExecutionRuntimeContext,
    ) -> ExecutionRuntimeContext:
        """Apply the configured RuntimeBindingPolicy to the runtime context.

        Returns the original runtime unchanged when:
          - no policy is configured (passthrough — adapters use defaults), OR
          - the runtime context already carries a binding (caller-supplied
            override wins; explicit > policy).

        Otherwise selects a binding via the policy and returns a new
        ExecutionRuntimeContext with ``runtime_binding`` populated.
        """
        if self._runtime_binding_policy is None:
            return runtime
        if runtime.runtime_binding is not None:
            # Caller already pinned a binding — respect it.
            return runtime

        try:
            cxrp_binding = self._runtime_binding_policy.select(
                bundle.proposal, bundle.decision,
            )
        except Exception as exc:
            logger.warning(
                "RuntimeBindingPolicy.select failed for proposal=%s — falling back to backend default: %s",
                bundle.proposal.proposal_id, exc,
            )
            return runtime

        if cxrp_binding is None:
            return runtime

        # Mirror the canonical CxRP RuntimeBinding into the OC summary type
        # carried on ExecutionRequest.
        from dataclasses import replace
        from operations_center.contracts.execution import RuntimeBindingSummary

        summary = RuntimeBindingSummary(
            kind=cxrp_binding.kind.value,
            selection_mode=cxrp_binding.selection_mode.value,
            model=cxrp_binding.model,
            provider=cxrp_binding.provider,
            endpoint=cxrp_binding.endpoint,
            config_ref=cxrp_binding.config_ref,
        )
        logger.info(
            "RuntimeBindingPolicy: bound runtime for proposal=%s to kind=%s model=%s provider=%s",
            bundle.proposal.proposal_id,
            summary.kind, summary.model, summary.provider,
        )
        return replace(runtime, runtime_binding=summary)

    def _record_run_memory(
        self,
        *,
        request: ExecutionRequest,
        result: ExecutionResult,
        bundle: ProposalDecisionBundle,
    ) -> None:
        if self._run_memory_index_dir is None:
            return
        try:
            from operations_center.run_memory import record_execution_result

            record_execution_result(
                result,
                self._run_memory_index_dir,
                repo_id=request.repo_key,
                tags=(
                    bundle.proposal.task_type.value,
                    bundle.decision.selected_lane.value,
                    bundle.decision.selected_backend.value,
                ),
            )
        except Exception as exc:
            logger.warning(
                "Run memory indexing failed for run %s: %s", request.run_id, exc,
            )

    def _evaluate_resource_gate(self) -> "BudgetDecision | None":
        """Return the first failing global gate, or None when all pass.

        Chain: total concurrency → free RAM. Returns None when no
        ``usage_store`` is configured or the gate is unset (preserves
        existing test fixtures that wire the coordinator without a
        gate). The gate exists to reserve headroom for co-tenant workloads
        audits running on the same host — when it fires, the dispatch
        is skipped with ``BUDGET_EXHAUSTED`` and the reason carries
        either ``global_concurrency_exceeded`` or
        ``global_memory_insufficient``.
        """
        if self._usage_store is None or self._resource_gate is None:
            return None
        gate = self._resource_gate
        now = datetime.now(UTC)
        if gate.max_concurrent is not None:
            d = self._usage_store.global_concurrency_decision(
                max_concurrent=gate.max_concurrent, now=now,
            )
            if not d.allowed:
                return d
        if gate.min_available_memory_mb is not None:
            d = self._usage_store.global_memory_decision(
                min_available_memory_mb=gate.min_available_memory_mb,
            )
            if not d.allowed:
                return d
        return None

    def _evaluate_backend_caps(
        self,
        backend_name: str,
    ) -> "BudgetDecision | None":
        """Return the first failing backend cap, or None when all pass.

        Chain: per-backend rate → concurrency → RAM. Returns None when:
          - no usage_store is configured (existing tests / unit-only paths)
          - the backend has no entry in ``backend_caps`` and no caps fire

        When a cap blocks, the returned ``BudgetDecision`` carries the
        ``reason`` / ``window`` / ``current`` / ``limit`` fields so the
        capped-result builder can surface *which* cap fired.
        """
        if self._usage_store is None:
            return None
        cap = self._backend_caps.get(backend_name)
        if cap is None:
            return None
        now = datetime.now(UTC)
        # Rate cap (hourly/daily) — uses the existing per-backend helper.
        if cap.max_per_hour is not None or cap.max_per_day is not None:
            d = self._usage_store.budget_decision_for_backend(
                backend_name,
                max_per_hour=cap.max_per_hour,
                max_per_day=cap.max_per_day,
                now=now,
            )
            if not d.allowed:
                return d
        # Concurrency cap.
        if cap.max_concurrent is not None:
            d = self._usage_store.concurrency_decision_for_backend(
                backend_name,
                max_concurrent=cap.max_concurrent,
                now=now,
            )
            if not d.allowed:
                return d
        # RAM threshold.
        if cap.min_available_memory_mb is not None:
            d = self._usage_store.memory_decision_for_backend(
                backend_name,
                min_available_memory_mb=cap.min_available_memory_mb,
                now=now,
            )
            if not d.allowed:
                return d
        return None

    def _log_contract_impact(self, request: ExecutionRequest) -> dict[str, Any]:
        """Pre-dispatch hook: log contract-change blast radius for ``request.repo_key``.

        Returns a metadata dict that will be merged into the observability
        record. Empty dict when no graph is configured, the repo_key
        doesn't resolve, or the target has no consumers.
        """
        if self._repo_graph is None:
            return {}
        try:
            from platform_manifest import RepoGraph

            from operations_center.impact_analysis import (
                compute_contract_impact,
            )
        except Exception:  # noqa: BLE001 — defensive: never block dispatch
            return {}

        if not isinstance(self._repo_graph, RepoGraph):
            return {}
        try:
            summary = compute_contract_impact(self._repo_graph, request.repo_key)
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "Contract impact analysis failed for repo_key=%r: %s",
                request.repo_key, exc,
            )
            return {}

        if summary is None or not summary.has_impact():
            return {}

        public = [n.canonical_name for n in summary.public_affected]
        private = [n.canonical_name for n in summary.private_affected]
        logger.info(
            "contract change in %s affects %d consumer(s) [public=%d private=%d]: %s",
            summary.target.canonical_name,
            len(summary.affected),
            len(public),
            len(private),
            ", ".join(n.canonical_name for n in summary.affected),
        )
        return {
            "contract_impact": {
                "target": summary.target.canonical_name,
                "target_repo_id": summary.target.repo_id,
                "affected_count": len(summary.affected),
                "public_affected": public,
                "private_affected": private,
            }
        }

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
            # G-V02 — surface SwitchBoard routing provenance in the
            # execution record metadata so audit consumers can answer
            # "which rule fired? why? from which switchboard version?"
            # without re-reading decision.json.
            "routing": {
                "decision_id": bundle.decision.decision_id,
                "selected_lane": bundle.decision.selected_lane.value,
                "selected_backend": bundle.decision.selected_backend.value,
                "policy_rule_matched": bundle.decision.policy_rule_matched,
                "rationale": bundle.decision.rationale,
                "switchboard_version": bundle.decision.switchboard_version,
                "confidence": bundle.decision.confidence,
                "alternatives_considered": [
                    lane.value for lane in bundle.decision.alternatives_considered
                ],
            },
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

        See ``docs/architecture/recovery/recovery_loop_design.md`` for the design.
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


def _resource_gate_blocked_result(
    request,
    decision: "BudgetDecision",
) -> ExecutionResult:
    """Build a SKIPPED result when the global resource gate blocks dispatch.

    The gate reserves host headroom for co-tenant workloads — when it
    fires, the dispatch isn't actually broken; the host just doesn't
    have the resources to safely take it on right now. Reason mirrors
    the BudgetDecision so observability + status panes can surface
    *which* gate fired (concurrency / RAM).
    """
    parts = [f"dispatch skipped — global resource gate {decision.reason}"]
    if decision.window:
        parts.append(f"window={decision.window}")
    if decision.current is not None and decision.limit is not None:
        parts.append(f"current={decision.current} limit={decision.limit}")
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.SKIPPED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=FailureReasonCategory.BUDGET_EXHAUSTED,
        failure_reason="; ".join(parts),
    )


def _backend_capped_result(
    request,
    decision: "BudgetDecision",
    backend_name: str,
) -> ExecutionResult:
    """Build a SKIPPED result when a per-backend cap blocks the dispatch.

    Reason mirrors the BudgetDecision so observability + status panes can
    surface *which* cap fired (rate / concurrency / RAM).
    """
    parts = [
        f"dispatch skipped — backend={backend_name!r} {decision.reason}",
    ]
    if decision.window:
        parts.append(f"window={decision.window}")
    if decision.current is not None and decision.limit is not None:
        parts.append(f"current={decision.current} limit={decision.limit}")
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.SKIPPED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=FailureReasonCategory.BUDGET_EXHAUSTED,
        failure_reason="; ".join(parts),
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


def _attach_lifecycle_outcome(
    *,
    request: ExecutionRequest,
    result: ExecutionResult,
    repo_graph_context: object | None,
) -> ExecutionResult:
    """Run plan + verify around the already-dispatched execution.

    The coordinator's dispatch IS the ``execute`` stage — we don't re-dispatch.
    Plan and verify use built-in default handlers; callers wanting custom
    behavior can subclass or override ``_DEFAULT_LIFECYCLE_HANDLERS`` later.
    Failures in plan/verify do not corrupt the canonical result; they are
    recorded on ``lifecycle_outcome`` only.
    """
    from operations_center.lifecycle import (
        Check,
        CheckResult,
        ExecuteOutput,
        LifecycleRunner,
        PlanOutput,
        StageHandlers,
    )

    def _default_plan(*, request, repo_graph_context):  # type: ignore[no-untyped-def]
        targets = []
        if repo_graph_context is not None and hasattr(repo_graph_context, "resolve"):
            node = repo_graph_context.resolve(request.repo_key)
            if node is not None:
                targets = [node.canonical_name]
        if not targets:
            targets = [request.repo_key]
        return PlanOutput(
            plan_summary=f"dispatch {request.repo_key} via coordinator",
            target_repos=targets,
            steps=["coordinator dispatch"],
            checks=[Check(check_id="execution_succeeded")],
        )

    def _default_execute(*, request, plan):  # type: ignore[no-untyped-def]
        # The actual execution already ran; mirror its status.
        return ExecuteOutput(
            result_ref=result.run_id,
            status=result.status.value if hasattr(result.status, "value") else str(result.status),
        )

    def _default_verify(*, request, plan, execution):  # type: ignore[no-untyped-def]
        passed = result.success
        return [
            CheckResult(
                check_id="execution_succeeded",
                passed=passed,
                reason=None if passed else (result.failure_reason or "execution did not succeed"),
            )
        ]

    metadata = request.lifecycle
    if metadata is None:
        # Caller is responsible for checking; this branch keeps the helper
        # safe to call defensively and satisfies the LifecycleRunner.run
        # signature (which requires non-None metadata).
        return result

    runner = LifecycleRunner(
        StageHandlers(plan=_default_plan, execute=_default_execute, verify=_default_verify)
    )
    try:
        lc_result = runner.run(
            request=request,
            metadata=metadata,
            repo_graph_context=repo_graph_context,
        )
    except Exception as exc:
        logger.warning(
            "Lifecycle runner raised for run %s: %s", request.run_id, exc,
        )
        return result
    return result.model_copy(update={"lifecycle_outcome": lc_result.outcome})


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
