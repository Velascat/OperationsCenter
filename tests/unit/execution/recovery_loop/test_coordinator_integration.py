# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Recovery-loop integration tests for ExecutionCoordinator (Phase 7 / spec)."""

from __future__ import annotations

from pathlib import Path

from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import (
    BackendName,
    ExecutionStatus,
    FailureReasonCategory,
    LaneName,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.contracts.routing import LaneDecision
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.execution.handoff import ExecutionRuntimeContext
from operations_center.execution.recovery_loop import (
    ExecutionFailureKind,
    NoPaidRetryBudgetChecker,
    RecoveryDecision,
    RecoveryEngine,
    RecoveryPolicy,
    RejectUnrecoverableHandler,
    RetrySameRequestHandler,
    DefaultFailureClassifier,
)
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.policy.models import PolicyDecision, PolicyStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _StubPolicyEngine:
    def __init__(self, decision: PolicyDecision, *, raise_on_call: int | None = None) -> None:
        self._decision = decision
        self._raise_on_call = raise_on_call
        self.called = 0

    def evaluate(self, proposal, decision, request=None) -> PolicyDecision:
        self.called += 1
        if self._raise_on_call is not None and self.called == self._raise_on_call:
            raise RuntimeError("policy engine boom")
        return self._decision


class _ScriptedAdapter:
    """Returns a sequence of results, one per call."""

    def __init__(self, results: list[ExecutionResult]) -> None:
        self._results = list(results)
        self.calls = 0
        self.last_request = None

    def execute(self, request):
        self.calls += 1
        self.last_request = request
        idx = min(self.calls - 1, len(self._results) - 1)
        return self._results[idx]


class _Registry:
    def __init__(self, adapter) -> None:
        self._adapter = adapter

    def for_backend(self, backend):
        return self._adapter


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
        decision=LaneDecision(
            proposal_id=proposal.proposal_id,
            selected_lane=LaneName.AIDER_LOCAL,
            selected_backend=BackendName.DIRECT_LOCAL,
        ),
    )


def _runtime() -> ExecutionRuntimeContext:
    return ExecutionRuntimeContext(
        workspace_path=Path("/tmp/workspace"),
        task_branch="auto/lint-fix",
    )


def _allow_policy() -> _StubPolicyEngine:
    return _StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW, notes="ok"))


def _success_result(bundle: ProposalDecisionBundle) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )


def _timeout_result(bundle: ProposalDecisionBundle) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.TIMED_OUT,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=FailureReasonCategory.TIMEOUT,
    )


def _validation_failed_result(bundle: ProposalDecisionBundle) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.FAILED),
        failure_category=FailureReasonCategory.VALIDATION_FAILED,
    )


def _backend_unavailable_result(bundle: ProposalDecisionBundle) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason="adapter_error_code=backend_unavailable: down",
    )


def _rate_limit_result_no_retry_after(bundle: ProposalDecisionBundle) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason="adapter_error_code=rate_limit: too many requests",
    )


def _build_coordinator(
    *,
    adapter,
    policy_engine,
    recovery_policy: RecoveryPolicy,
    budget_checker=None,
) -> ExecutionCoordinator:
    handlers = [
        RetrySameRequestHandler(recovery_policy.retryable_kinds),
        RejectUnrecoverableHandler(recovery_policy.non_retryable_kinds),
    ]
    engine = RecoveryEngine(
        classifier=DefaultFailureClassifier(),
        policy=recovery_policy,
        handlers=handlers,
        budget_checker=budget_checker,
    )
    return ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=policy_engine,
        recovery_engine=engine,
        recovery_policy=recovery_policy,
    )


# ---------------------------------------------------------------------------
# Test cases (ordered to match spec)
# ---------------------------------------------------------------------------


class TestSuccessOnFirstAttempt:
    def test_adapter_called_once_recovery_attempts_one(self):
        bundle = _bundle()
        adapter = _ScriptedAdapter([_success_result(bundle)])
        coord = _build_coordinator(
            adapter=adapter,
            policy_engine=_allow_policy(),
            recovery_policy=RecoveryPolicy(max_attempts=3),
        )
        outcome = coord.execute(bundle, _runtime())
        assert adapter.calls == 1
        assert outcome.result.success is True
        assert outcome.result.recovery is not None
        assert outcome.result.recovery.attempts == 1
        assert outcome.result.recovery.final_decision == RecoveryDecision.ACCEPT.value


class TestIdempotentTransientThenSuccess:
    def test_idempotent_timeout_then_success_retries(self):
        bundle = _bundle()
        adapter = _ScriptedAdapter([
            _timeout_result(bundle),
            _success_result(bundle),
        ])

        # Need to thread idempotent=True through the request. Since the
        # default builder does not set this, we use a stub builder that
        # marks the request idempotent.
        class _IdempotentBuilder:
            def build(self, bundle, runtime, policy_decision=None):
                return ExecutionRequest(
                    proposal_id=bundle.proposal.proposal_id,
                    decision_id=bundle.decision.decision_id,
                    goal_text=bundle.proposal.goal_text,
                    repo_key="svc",
                    clone_url="https://example.invalid/svc.git",
                    base_branch="main",
                    task_branch="auto/lint",
                    workspace_path=Path("/tmp/ws"),
                    idempotent=True,
                )

        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow_policy(),
            request_builder=_IdempotentBuilder(),
            recovery_policy=RecoveryPolicy(max_attempts=3),
        )
        outcome = coord.execute(bundle, _runtime())
        assert adapter.calls == 2
        assert outcome.result.success is True
        assert outcome.result.recovery.attempts == 2
        assert outcome.result.recovery.final_decision == RecoveryDecision.ACCEPT.value


class TestNonIdempotentTransientNoRetry:
    def test_non_idempotent_timeout_does_not_retry(self):
        bundle = _bundle()
        adapter = _ScriptedAdapter([_timeout_result(bundle)])
        coord = _build_coordinator(
            adapter=adapter,
            policy_engine=_allow_policy(),
            recovery_policy=RecoveryPolicy(max_attempts=3),
        )
        outcome = coord.execute(bundle, _runtime())
        # Default request is non-idempotent → STOP_IDEMPOTENCY_REQUIRED on first failure.
        assert adapter.calls == 1
        assert outcome.result.recovery.final_decision == RecoveryDecision.STOP_IDEMPOTENCY_REQUIRED.value


class TestRepeatedTimeoutExhaustsBudget:
    def test_idempotent_repeated_timeout_exhausts(self):
        bundle = _bundle()
        adapter = _ScriptedAdapter([_timeout_result(bundle)] * 5)

        class _IdempotentBuilder:
            def build(self, bundle, runtime, policy_decision=None):
                return ExecutionRequest(
                    proposal_id=bundle.proposal.proposal_id,
                    decision_id=bundle.decision.decision_id,
                    goal_text=bundle.proposal.goal_text,
                    repo_key="svc", clone_url="https://x/y.git",
                    base_branch="main", task_branch="auto/x",
                    workspace_path=Path("/tmp/ws"),
                    idempotent=True,
                )

        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow_policy(),
            request_builder=_IdempotentBuilder(),
            recovery_policy=RecoveryPolicy(max_attempts=3),
        )
        outcome = coord.execute(bundle, _runtime())
        assert adapter.calls == 3
        assert outcome.result.recovery.attempts == 3
        assert outcome.result.recovery.final_decision == RecoveryDecision.STOP_ATTEMPT_BUDGET_EXHAUSTED.value


class TestContractViolationRejects:
    def test_contract_violation_does_not_retry(self):
        bundle = _bundle()
        adapter = _ScriptedAdapter([_validation_failed_result(bundle)])
        coord = _build_coordinator(
            adapter=adapter,
            policy_engine=_allow_policy(),
            recovery_policy=RecoveryPolicy(max_attempts=3),
        )
        outcome = coord.execute(bundle, _runtime())
        assert adapter.calls == 1
        assert outcome.result.recovery.final_decision == RecoveryDecision.REJECT_UNRECOVERABLE.value


class TestRateLimitWithoutBackoff:
    def test_rate_limit_without_retry_after_does_not_retry(self):
        bundle = _bundle()
        adapter = _ScriptedAdapter([_rate_limit_result_no_retry_after(bundle)])
        # Need RATE_LIMIT in retryable kinds for the engine to even consider it.
        policy = RecoveryPolicy(
            max_attempts=3,
            retryable_kinds=frozenset({
                ExecutionFailureKind.TRANSIENT,
                ExecutionFailureKind.TIMEOUT,
                ExecutionFailureKind.BACKEND_UNAVAILABLE,
                ExecutionFailureKind.RATE_LIMIT,
            }),
        )

        class _IdempotentBuilder:
            def build(self, bundle, runtime, policy_decision=None):
                return ExecutionRequest(
                    proposal_id=bundle.proposal.proposal_id,
                    decision_id=bundle.decision.decision_id,
                    goal_text=bundle.proposal.goal_text,
                    repo_key="svc", clone_url="https://x/y.git",
                    base_branch="main", task_branch="auto/x",
                    workspace_path=Path("/tmp/ws"),
                    idempotent=True,
                )

        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow_policy(),
            request_builder=_IdempotentBuilder(),
            recovery_policy=policy,
        )
        outcome = coord.execute(bundle, _runtime())
        assert adapter.calls == 1
        assert outcome.result.recovery.final_decision == RecoveryDecision.STOP_BACKOFF_REQUIRED.value


class TestCostBudgetExhausted:
    def test_no_paid_budget_blocks_retry(self):
        bundle = _bundle()
        adapter = _ScriptedAdapter([_timeout_result(bundle)])

        class _IdempotentBuilder:
            def build(self, bundle, runtime, policy_decision=None):
                return ExecutionRequest(
                    proposal_id=bundle.proposal.proposal_id,
                    decision_id=bundle.decision.decision_id,
                    goal_text=bundle.proposal.goal_text,
                    repo_key="svc", clone_url="https://x/y.git",
                    base_branch="main", task_branch="auto/x",
                    workspace_path=Path("/tmp/ws"),
                    idempotent=True,
                )

        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow_policy(),
            request_builder=_IdempotentBuilder(),
            recovery_policy=RecoveryPolicy(max_attempts=3),
            recovery_engine=RecoveryEngine(
                classifier=DefaultFailureClassifier(),
                policy=RecoveryPolicy(max_attempts=3),
                handlers=[
                    RetrySameRequestHandler(RecoveryPolicy().retryable_kinds),
                    RejectUnrecoverableHandler(RecoveryPolicy().non_retryable_kinds),
                ],
                budget_checker=NoPaidRetryBudgetChecker(),
            ),
        )
        outcome = coord.execute(bundle, _runtime())
        assert adapter.calls == 1
        assert outcome.result.recovery.final_decision == RecoveryDecision.STOP_COST_BUDGET_EXHAUSTED.value


class TestDefensivePolicyEngineCrash:
    def test_policy_engine_raises_returns_failed_result(self):
        bundle = _bundle()
        adapter = _ScriptedAdapter([_backend_unavailable_result(bundle)] * 3)
        # Policy raises on the SECOND call (first call is the upfront validate;
        # second is the post-retry revalidate triggered by requires_policy_revalidation).
        policy = _StubPolicyEngine(
            PolicyDecision(status=PolicyStatus.ALLOW, notes="ok"),
            raise_on_call=2,
        )

        # Need a builder that produces a non-idempotent request and a handler
        # that returns RETRY_MODIFIED_REQUEST so revalidation is triggered.
        from operations_center.execution.recovery_loop import RecoveryAction, RecoveryOutcome

        class _ModifyingHandler:
            name = "modifying"

            def recover(self, failure_kind, result, context):
                return RecoveryOutcome(
                    decision=RecoveryDecision.RETRY_MODIFIED_REQUEST,
                    action=RecoveryAction(
                        attempt=context.attempt,
                        failure_kind=failure_kind,
                        decision=RecoveryDecision.RETRY_MODIFIED_REQUEST,
                        reason="modify and retry",
                        handler_name=self.name,
                    ),
                    next_request=ExecutionRequest(
                        proposal_id=context.original_request.proposal_id,
                        decision_id=context.original_request.decision_id,
                        goal_text=context.original_request.goal_text,
                        repo_key=context.original_request.repo_key,
                        clone_url=context.original_request.clone_url,
                        base_branch=context.original_request.base_branch,
                        task_branch="modified-branch",
                        workspace_path=context.original_request.workspace_path,
                        idempotent=False,
                    ),
                    requires_policy_revalidation=True,
                )

        policy_def = RecoveryPolicy(
            max_attempts=3,
            retryable_kinds=frozenset({ExecutionFailureKind.BACKEND_UNAVAILABLE}),
            pre_send_failure_kinds=frozenset({ExecutionFailureKind.BACKEND_UNAVAILABLE}),
        )
        engine = RecoveryEngine(
            classifier=DefaultFailureClassifier(),
            policy=policy_def,
            handlers=[_ModifyingHandler(), RejectUnrecoverableHandler(policy_def.non_retryable_kinds)],
        )

        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=policy,
            recovery_policy=policy_def,
            recovery_engine=engine,
        )
        outcome = coord.execute(bundle, _runtime())
        # Adapter ran once; the modify-retry triggered a revalidation that raised.
        assert outcome.executed is True
        # Result is failed (synthetic POLICY_BLOCKED-style result).
        assert outcome.result.success is False
        assert outcome.result.failure_category == FailureReasonCategory.POLICY_BLOCKED
        assert "raised mid-loop" in (outcome.result.failure_reason or "")


class TestDefensiveRecoveryEngineCrash:
    def test_recovery_engine_raises_returns_failed_result(self):
        bundle = _bundle()
        adapter = _ScriptedAdapter([_timeout_result(bundle)])

        class _BoomEngine:
            def evaluate(self, result, context):
                raise RuntimeError("recovery boom")

        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_allow_policy(),
            recovery_policy=RecoveryPolicy(max_attempts=3),
            recovery_engine=_BoomEngine(),
        )
        outcome = coord.execute(bundle, _runtime())
        assert adapter.calls == 1
        assert outcome.result.success is False
        assert outcome.result.failure_category == FailureReasonCategory.BACKEND_ERROR
        assert "RecoveryEngine raised" in (outcome.result.failure_reason or "")
        # The synthetic recovery action was attached.
        assert outcome.result.recovery is not None
        assert outcome.result.recovery.final_decision == RecoveryDecision.REJECT_UNRECOVERABLE.value


class TestBackendUnavailableAllowsNonIdempotentRetry:
    def test_pre_send_failure_kind_allows_non_idempotent_retry(self):
        bundle = _bundle()
        adapter = _ScriptedAdapter([
            _backend_unavailable_result(bundle),
            _success_result(bundle),
        ])
        # Default RecoveryPolicy already has BACKEND_UNAVAILABLE in
        # retryable_kinds AND in pre_send_failure_kinds, so a non-idempotent
        # request can retry.
        coord = _build_coordinator(
            adapter=adapter,
            policy_engine=_allow_policy(),
            recovery_policy=RecoveryPolicy(max_attempts=3),
        )
        outcome = coord.execute(bundle, _runtime())
        assert adapter.calls == 2
        assert outcome.result.success is True
