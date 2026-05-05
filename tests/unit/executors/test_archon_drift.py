# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Per-backend drift verification for Archon.

Multi-agent backend — exercises all four drift kinds including
internal routing.
"""
from __future__ import annotations

from operations_center.drift.testing import DriftInjectionFixture


def _fix() -> DriftInjectionFixture:
    return DriftInjectionFixture(backend_id="archon", request_id="archon-test-req")


def test_archon_runtime_drift_fires():
    f = _fix().inject_runtime(
        bound={"kind": "cli_subscription", "model": "opus"},
        observed={"kind": "hosted_api", "model": "gpt-4"},
    )
    assert f is not None and f.drift_type.value == "runtime"


def test_archon_capability_drift_fires():
    f = _fix().inject_capability(
        allowed={"repo_read", "test_run"},
        used={"repo_read", "network_access"},
    )
    assert f is not None and f.drift_type.value == "capability"


def test_archon_output_shape_drift_fires():
    f = _fix().inject_output_shape(
        result_payload={
            "schema_version": "0.2", "contract_kind": "execution_result",
            "result_id": "r", "request_id": "q", "ok": True, "status": "succeeded",
            "workflow_events": "leaked at top level",
        },
    )
    assert f is not None and f.drift_type.value == "output_shape"


def test_archon_internal_routing_drift_fires():
    f = _fix().inject_internal_routing(
        bound={"planner": "opus", "executor": "sonnet", "critic": "haiku"},
        observed={"planner": "opus", "executor": "gpt-4", "critic": "haiku"},
    )
    assert f is not None
    assert f.drift_type.value == "internal_routing"
    assert "executor" in str(f.impact)
