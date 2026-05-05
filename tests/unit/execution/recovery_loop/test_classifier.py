# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for DefaultFailureClassifier."""

from __future__ import annotations

from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.execution.recovery_loop import (
    DefaultFailureClassifier,
    ExecutionFailureKind,
    RecoveryContext,
)


def _ctx(req):
    return RecoveryContext(
        original_request=req,
        current_request=req,
        attempt=1,
        previous_actions=(),
    )


class TestDefaultFailureClassifier:
    def test_success_returns_none(self, make_request, make_result):
        c = DefaultFailureClassifier()
        req = make_request()
        res = make_result(request=req, success=True, status=ExecutionStatus.SUCCEEDED)
        assert c.classify(res, _ctx(req)) == ExecutionFailureKind.NONE

    def test_timed_out_status_maps_to_timeout(self, make_request, make_result):
        c = DefaultFailureClassifier()
        req = make_request()
        res = make_result(request=req, status=ExecutionStatus.TIMED_OUT)
        assert c.classify(res, _ctx(req)) == ExecutionFailureKind.TIMEOUT

    def test_timeout_failure_category_maps_to_timeout(self, make_request, make_result):
        c = DefaultFailureClassifier()
        req = make_request()
        res = make_result(request=req, failure_category=FailureReasonCategory.TIMEOUT)
        assert c.classify(res, _ctx(req)) == ExecutionFailureKind.TIMEOUT

    def test_validation_failed_maps_to_contract_violation(self, make_request, make_result):
        c = DefaultFailureClassifier()
        req = make_request()
        res = make_result(request=req, failure_category=FailureReasonCategory.VALIDATION_FAILED)
        assert c.classify(res, _ctx(req)) == ExecutionFailureKind.CONTRACT_VIOLATION

    def test_policy_blocked_maps_to_configuration(self, make_request, make_result):
        c = DefaultFailureClassifier()
        req = make_request()
        res = make_result(request=req, failure_category=FailureReasonCategory.POLICY_BLOCKED)
        assert c.classify(res, _ctx(req)) == ExecutionFailureKind.CONFIGURATION

    def test_routing_error_maps_to_configuration(self, make_request, make_result):
        c = DefaultFailureClassifier()
        req = make_request()
        res = make_result(request=req, failure_category=FailureReasonCategory.ROUTING_ERROR)
        assert c.classify(res, _ctx(req)) == ExecutionFailureKind.CONFIGURATION

    def test_adapter_rate_limit_code(self, make_request, make_result):
        c = DefaultFailureClassifier()
        req = make_request()
        res = make_result(
            request=req,
            failure_category=FailureReasonCategory.BACKEND_ERROR,
            failure_reason="adapter_error_code=rate_limit: too many requests",
        )
        assert c.classify(res, _ctx(req)) == ExecutionFailureKind.RATE_LIMIT

    def test_adapter_backend_unavailable_code(self, make_request, make_result):
        c = DefaultFailureClassifier()
        req = make_request()
        res = make_result(
            request=req,
            failure_category=FailureReasonCategory.BACKEND_ERROR,
            failure_reason="adapter_error_code=backend_unavailable: connection refused",
        )
        assert c.classify(res, _ctx(req)) == ExecutionFailureKind.BACKEND_UNAVAILABLE

    def test_adapter_auth_failed_code(self, make_request, make_result):
        c = DefaultFailureClassifier()
        req = make_request()
        res = make_result(
            request=req,
            failure_category=FailureReasonCategory.BACKEND_ERROR,
            failure_reason="adapter_error_code=auth_failed: 401",
        )
        assert c.classify(res, _ctx(req)) == ExecutionFailureKind.AUTH

    def test_unknown_failure_returns_unknown(self, make_request, make_result):
        c = DefaultFailureClassifier()
        req = make_request()
        res = make_result(request=req, failure_category=FailureReasonCategory.BACKEND_ERROR)
        assert c.classify(res, _ctx(req)) == ExecutionFailureKind.UNKNOWN

    def test_unrecognized_adapter_code_falls_through_to_unknown(self, make_request, make_result):
        c = DefaultFailureClassifier()
        req = make_request()
        res = make_result(
            request=req,
            failure_category=FailureReasonCategory.BACKEND_ERROR,
            failure_reason="adapter_error_code=mystery: something",
        )
        assert c.classify(res, _ctx(req)) == ExecutionFailureKind.UNKNOWN
