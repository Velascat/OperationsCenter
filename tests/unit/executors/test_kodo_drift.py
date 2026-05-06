# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Per-backend drift verification for Kodo.

Per the spec, ``audit_verdict.yaml::drift_detection: PASS`` is only
valid if the four drift detectors fire correctly for this backend via
synthetic injection. Internal routing is N/A for Kodo (single-agent).
"""
from __future__ import annotations

from operations_center.drift.testing import DriftInjectionFixture


def _fix() -> DriftInjectionFixture:
    return DriftInjectionFixture(backend_id="kodo", request_id="kodo-test-req")


def test_kodo_runtime_drift_fires():
    f = _fix().inject_runtime(
        bound={"kind": "cli_subscription", "model": "opus"},
        observed={"kind": "cli_subscription", "model": "haiku"},
    )
    assert f is not None and f.drift_type.value == "runtime"


def test_kodo_capability_drift_fires():
    f = _fix().inject_capability(
        allowed={"repo_read", "repo_patch"},
        used={"repo_read", "shell_write"},
    )
    assert f is not None and f.drift_type.value == "capability"


def test_kodo_output_shape_drift_fires():
    f = _fix().inject_output_shape(
        result_payload={
            "schema_version": "0.3", "contract_kind": "execution_result",
            "result_id": "r", "request_id": "q", "ok": True, "status": "succeeded",
            "kodo_team_used": "leaked",
        },
    )
    assert f is not None and f.drift_type.value == "output_shape"


def test_kodo_internal_routing_is_na():
    """Kodo is single-agent — internal routing drift is not applicable.
    ``audit_verdict.yaml`` should mark internal_routing as N/A."""
    # No assertion here beyond intent — N/A means we don't run the
    # injection. This test exists to make N/A explicit per spec.
    assert True
