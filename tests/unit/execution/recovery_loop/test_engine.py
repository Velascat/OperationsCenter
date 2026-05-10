# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for RecoveryEngine evaluation rules."""

from __future__ import annotations

from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.contracts.execution import RuntimeBindingSummary
from operations_center.backend_health import BackendHealthRegistry, BackendHealthState
from operations_center.execution.recovery_loop import (
    DefaultFailureClassifier,
    ExecutionFailureKind,
    NoPaidRetryBudgetChecker,
    RecoveryContext,
    RecoveryDecision,
    RecoveryEngine,
    RecoveryPolicy,
    RejectUnrecoverableHandler,
    RetrySameRequestHandler,
)


def _engine(policy: RecoveryPolicy, *, budget_checker=None) -> RecoveryEngine:
    return RecoveryEngine(
        classifier=DefaultFailureClassifier(),
        policy=policy,
        handlers=[
            RetrySameRequestHandler(policy.retryable_kinds),
            RejectUnrecoverableHandler(policy.non_retryable_kinds),
        ],
        budget_checker=budget_checker,
    )


def _ctx(req, *, attempt: int = 1, previous=()):
    return RecoveryContext(
        original_request=req,
        current_request=req,
        attempt=attempt,
        previous_actions=previous,
    )


class TestEngineSuccess:
    def test_successful_result_returns_accept(self, make_request, make_result):
        eng = _engine(RecoveryPolicy())
        req = make_request()
        res = make_result(request=req, success=True, status=ExecutionStatus.SUCCEEDED)
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.ACCEPT
        assert out.next_request is None


class TestEngineRetry:
    def test_idempotent_timeout_retries(self, make_request, make_result):
        eng = _engine(RecoveryPolicy(max_attempts=3))
        req = make_request(idempotent=True)
        res = make_result(request=req, status=ExecutionStatus.TIMED_OUT)
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.RETRY_SAME_REQUEST
        assert out.next_request is req

    def test_non_idempotent_timeout_stops_with_idempotency_required(self, make_request, make_result):
        eng = _engine(RecoveryPolicy(max_attempts=3))
        req = make_request(idempotent=False)
        res = make_result(request=req, status=ExecutionStatus.TIMED_OUT)
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.STOP_IDEMPOTENCY_REQUIRED

    def test_pre_send_failure_allows_non_idempotent_retry(self, make_request, make_result):
        eng = _engine(RecoveryPolicy(max_attempts=3))
        req = make_request(idempotent=False)
        res = make_result(
            request=req,
            failure_category=FailureReasonCategory.BACKEND_ERROR,
            failure_reason="adapter_error_code=backend_unavailable: down",
        )
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.RETRY_SAME_REQUEST

    def test_attempt_budget_exhausted(self, make_request, make_result):
        eng = _engine(RecoveryPolicy(max_attempts=2))
        req = make_request(idempotent=True)
        res = make_result(request=req, status=ExecutionStatus.TIMED_OUT)
        out = eng.evaluate(res, _ctx(req, attempt=2))
        assert out.decision == RecoveryDecision.STOP_ATTEMPT_BUDGET_EXHAUSTED

    def test_sigkill_records_backend_cooldown_and_stops_retry(self, make_request, make_result):
        registry = BackendHealthRegistry(cooldown_seconds=1800)
        policy = RecoveryPolicy(max_attempts=3)
        eng = RecoveryEngine(
            classifier=DefaultFailureClassifier(),
            policy=policy,
            handlers=[
                RetrySameRequestHandler(policy.retryable_kinds),
                RejectUnrecoverableHandler(policy.non_retryable_kinds),
            ],
            backend_health_registry=registry,
        )
        req = make_request(
            idempotent=True,
            runtime_binding=RuntimeBindingSummary(
                kind="kodo",
                selection_mode="fixed",
            ),
        )
        res = make_result(
            request=req,
            status=ExecutionStatus.FAILED,
            failure_category=FailureReasonCategory.BACKEND_ERROR,
            failure_reason="adapter_error_code=executor_error: signal=SIGKILL",
        )

        out = eng.evaluate(res, _ctx(req))

        assert out.decision == RecoveryDecision.STOP_COOLDOWN_REQUIRED
        assert registry.get("kodo").state == BackendHealthState.UNSTABLE


class TestEngineReject:
    def test_contract_violation_rejects(self, make_request, make_result):
        eng = _engine(RecoveryPolicy(max_attempts=3))
        req = make_request(idempotent=True)
        res = make_result(request=req, failure_category=FailureReasonCategory.VALIDATION_FAILED)
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.REJECT_UNRECOVERABLE

    def test_auth_rejects(self, make_request, make_result):
        eng = _engine(RecoveryPolicy(max_attempts=3))
        req = make_request(idempotent=True)
        res = make_result(
            request=req,
            failure_category=FailureReasonCategory.BACKEND_ERROR,
            failure_reason="adapter_error_code=auth_failed: 401",
        )
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.REJECT_UNRECOVERABLE

    def test_unknown_rejects_by_default(self, make_request, make_result):
        eng = _engine(RecoveryPolicy(max_attempts=3))
        req = make_request(idempotent=True)
        res = make_result(request=req, failure_category=FailureReasonCategory.BACKEND_ERROR)
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.REJECT_UNRECOVERABLE

    def test_unknown_can_retry_when_policy_opts_in(self, make_request, make_result):
        eng = _engine(
            RecoveryPolicy(max_attempts=3, retry_unknowns=True, unknown_retry_limit=2)
        )
        req = make_request(idempotent=True)
        res = make_result(request=req, failure_category=FailureReasonCategory.BACKEND_ERROR)
        # Unknown is in non_retryable_kinds by default; if retry_unknowns True we
        # bypass the unknown short-circuit, but the non_retryable_kinds gate
        # still rejects. So the policy must also remove UNKNOWN from non_retryable.
        from operations_center.execution.recovery_loop import RecoveryPolicy as RP
        eng = _engine(
            RP(
                max_attempts=3,
                retry_unknowns=True,
                unknown_retry_limit=2,
                non_retryable_kinds=frozenset({
                    ExecutionFailureKind.AUTH,
                    ExecutionFailureKind.CONTRACT_VIOLATION,
                    ExecutionFailureKind.CONFIGURATION,
                }),
                retryable_kinds=frozenset({
                    ExecutionFailureKind.TRANSIENT,
                    ExecutionFailureKind.TIMEOUT,
                    ExecutionFailureKind.BACKEND_UNAVAILABLE,
                    ExecutionFailureKind.UNKNOWN,
                }),
            )
        )
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.RETRY_SAME_REQUEST


class TestEngineCostBudget:
    def test_no_paid_budget_blocks_retry(self, make_request, make_result):
        eng = _engine(RecoveryPolicy(max_attempts=3), budget_checker=NoPaidRetryBudgetChecker())
        req = make_request(idempotent=True)
        res = make_result(request=req, status=ExecutionStatus.TIMED_OUT)
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.STOP_COST_BUDGET_EXHAUSTED


class TestEngineRateLimit:
    def _allow_rate_limit_policy(self) -> RecoveryPolicy:
        return RecoveryPolicy(
            max_attempts=3,
            retryable_kinds=frozenset({
                ExecutionFailureKind.TRANSIENT,
                ExecutionFailureKind.TIMEOUT,
                ExecutionFailureKind.BACKEND_UNAVAILABLE,
                ExecutionFailureKind.RATE_LIMIT,
            }),
        )

    def test_rate_limit_without_retry_after_stops_with_backoff_required(self, make_request, make_result):
        eng = _engine(self._allow_rate_limit_policy())
        req = make_request(idempotent=True)
        res = make_result(
            request=req,
            failure_category=FailureReasonCategory.BACKEND_ERROR,
            failure_reason="adapter_error_code=rate_limit: too many requests",
        )
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.STOP_BACKOFF_REQUIRED

    def test_rate_limit_with_usable_retry_after_retries_with_delay(self, make_request, make_result):
        eng = _engine(self._allow_rate_limit_policy())
        req = make_request(idempotent=True)
        res = make_result(
            request=req,
            failure_category=FailureReasonCategory.BACKEND_ERROR,
            failure_reason="adapter_error_code=rate_limit: too many requests retry_after=5",
        )
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.RETRY_SAME_REQUEST
        assert out.delay_seconds == 5.0

    def test_rate_limit_with_excessive_retry_after_stops(self, make_request, make_result):
        eng = _engine(self._allow_rate_limit_policy())
        req = make_request(idempotent=True)
        res = make_result(
            request=req,
            failure_category=FailureReasonCategory.BACKEND_ERROR,
            failure_reason="adapter_error_code=rate_limit: retry_after=600",
        )
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.STOP_BACKOFF_REQUIRED


class TestEngineNoMatchingHandler:
    def test_no_handler_rejects(self, make_request, make_result):
        # Empty handler list — engine should fall through to REJECT.
        eng = RecoveryEngine(
            classifier=DefaultFailureClassifier(),
            policy=RecoveryPolicy(max_attempts=3),
            handlers=[],
        )
        req = make_request(idempotent=True)
        res = make_result(request=req, status=ExecutionStatus.TIMED_OUT)
        out = eng.evaluate(res, _ctx(req))
        assert out.decision == RecoveryDecision.REJECT_UNRECOVERABLE
