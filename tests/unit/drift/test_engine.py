# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Drift engine tests — the four detectors + finding shape."""
from __future__ import annotations

from operations_center.drift import (
    BackendDriftFinding,
    DriftKind,
    detect_capability_drift,
    detect_output_shape_drift,
    detect_runtime_drift,
)
from operations_center.drift.engine import detect_internal_routing_drift


# ── BACKEND_DRIFT finding shape ─────────────────────────────────────────


def test_finding_has_required_payload_fields():
    f = BackendDriftFinding(
        backend_id="x", request_id="r", drift_type=DriftKind.RUNTIME,
        observed={}, bound_or_allowed={}, impact="i",
    )
    d = f.to_dict()
    for k in ("backend_id", "request_id", "drift_type", "observed", "bound_or_allowed", "impact"):
        assert k in d
    assert d["drift_type"] == "runtime"
    assert f.rule == "BACKEND_DRIFT"


# ── runtime drift ───────────────────────────────────────────────────────


class TestRuntimeDrift:
    def test_no_drift_when_observed_matches(self):
        out = detect_runtime_drift(
            backend_id="kodo", request_id="r",
            bound_runtime={"kind": "cli_subscription", "model": "opus"},
            observed_runtime={"kind": "cli_subscription", "model": "opus"},
        )
        assert out is None

    def test_drift_when_model_differs(self):
        out = detect_runtime_drift(
            backend_id="kodo", request_id="r",
            bound_runtime={"kind": "cli_subscription", "model": "opus"},
            observed_runtime={"kind": "cli_subscription", "model": "sonnet"},
        )
        assert out is not None
        assert out.drift_type == DriftKind.RUNTIME
        assert "model" in out.impact

    def test_no_drift_when_observed_omits_field(self):
        # Observed didn't report the field — can't claim drift.
        out = detect_runtime_drift(
            backend_id="x", request_id="r",
            bound_runtime={"model": "opus"},
            observed_runtime={},
        )
        assert out is None


# ── capability drift ────────────────────────────────────────────────────


class TestCapabilityDrift:
    def test_no_drift_when_used_subset(self):
        out = detect_capability_drift(
            backend_id="x", request_id="r",
            allowed_capabilities={"repo_read", "repo_patch"},
            used_capabilities={"repo_read"},
        )
        assert out is None

    def test_drift_when_unauthorized_used(self):
        out = detect_capability_drift(
            backend_id="x", request_id="r",
            allowed_capabilities={"repo_read"},
            used_capabilities={"repo_read", "shell_write"},
        )
        assert out is not None
        assert out.drift_type == DriftKind.CAPABILITY
        assert "shell_write" in str(out.impact)


# ── output shape drift ──────────────────────────────────────────────────


class TestOutputShapeDrift:
    def test_no_drift_for_clean_payload(self):
        out = detect_output_shape_drift(
            backend_id="x", request_id="r",
            result_payload={
                "schema_version": "0.3", "contract_kind": "execution_result",
                "result_id": "r", "request_id": "rq", "ok": True,
                "status": "succeeded", "evidence": {},
            },
        )
        assert out is None

    def test_drift_for_extra_top_level_field(self):
        out = detect_output_shape_drift(
            backend_id="x", request_id="r",
            result_payload={
                "schema_version": "0.3", "contract_kind": "execution_result",
                "result_id": "r", "request_id": "rq", "ok": True,
                "status": "succeeded",
                "vendor_specific_top_level": "leaked",
            },
        )
        assert out is not None
        assert out.drift_type == DriftKind.OUTPUT_SHAPE
        assert "vendor_specific_top_level" in str(out.observed)


# ── internal routing drift ──────────────────────────────────────────────


class TestInternalRoutingDrift:
    def test_no_drift_when_agents_use_pinned_models(self):
        out = detect_internal_routing_drift(
            backend_id="archon", request_id="r",
            bound_agent_models={"planner": "opus", "executor": "sonnet"},
            observed_agent_models={"planner": "opus", "executor": "sonnet"},
        )
        assert out is None

    def test_drift_when_agent_uses_different_model(self):
        out = detect_internal_routing_drift(
            backend_id="archon", request_id="r",
            bound_agent_models={"planner": "opus"},
            observed_agent_models={"planner": "sonnet"},
        )
        assert out is not None
        assert out.drift_type == DriftKind.INTERNAL_ROUTING
        assert "planner" in str(out.impact)
