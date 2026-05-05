# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""DriftInjectionFixture — shared fixture for per-backend drift verification.

Per the spec (System Phase): every backend must include synthetic drift
tests using this fixture. The fixture parameterizes the four drift
kinds and asserts the engine fires the correct ``BACKEND_DRIFT`` finding.

Usage in a backend's drift test:

    from operations_center.drift.testing import DriftInjectionFixture

    def test_kodo_drift_runtime():
        fix = DriftInjectionFixture(backend_id="kodo", request_id="req-1")
        finding = fix.inject_runtime(
            bound={"kind": "cli_subscription", "model": "opus"},
            observed={"kind": "cli_subscription", "model": "sonnet"},
        )
        assert finding is not None
        assert finding.drift_type.value == "runtime"
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from operations_center.drift.engine import (
    BackendDriftFinding,
    detect_capability_drift,
    detect_internal_routing_drift,
    detect_output_shape_drift,
    detect_runtime_drift,
)


@dataclass
class DriftInjectionFixture:
    """Wraps the four engine detectors with a per-backend / per-request scope.

    Each ``inject_*`` method returns the finding (or None) for the same
    backend/request id pair, so backend tests don't repeat boilerplate.
    """

    backend_id: str
    request_id: str = "test-request"

    def inject_runtime(
        self,
        *,
        bound: dict[str, Any],
        observed: dict[str, Any],
    ) -> Optional[BackendDriftFinding]:
        return detect_runtime_drift(
            backend_id=self.backend_id,
            request_id=self.request_id,
            bound_runtime=bound,
            observed_runtime=observed,
        )

    def inject_capability(
        self,
        *,
        allowed: set[str],
        used: set[str],
    ) -> Optional[BackendDriftFinding]:
        return detect_capability_drift(
            backend_id=self.backend_id,
            request_id=self.request_id,
            allowed_capabilities=allowed,
            used_capabilities=used,
        )

    def inject_output_shape(
        self,
        *,
        result_payload: dict[str, Any],
    ) -> Optional[BackendDriftFinding]:
        return detect_output_shape_drift(
            backend_id=self.backend_id,
            request_id=self.request_id,
            result_payload=result_payload,
        )

    def inject_internal_routing(
        self,
        *,
        bound: dict[str, str],
        observed: dict[str, str],
    ) -> Optional[BackendDriftFinding]:
        return detect_internal_routing_drift(
            backend_id=self.backend_id,
            request_id=self.request_id,
            bound_agent_models=bound,
            observed_agent_models=observed,
        )
