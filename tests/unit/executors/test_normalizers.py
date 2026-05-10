# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Normalizer tests — Phase 2 enforcement: no shape leaks past this layer."""
from __future__ import annotations

import pytest
from dataclasses import fields

from cxrp.contracts import Evidence, ExecutionResult
from cxrp.validation.json_schema import validate_contract
from cxrp.vocabulary.status import ExecutionStatus

from operations_center.executors.kodo.normalizer import (
    NormalizationError as KodoNormErr,
    normalize as normalize_kodo,
)
from operations_center.executors.archon.normalizer import (
    normalize as normalize_archon,
)


_CXRP_RESULT_FIELDS = {f.name for f in fields(ExecutionResult)}
_CXRP_EVIDENCE_FIELDS = {f.name for f in fields(Evidence)}


# ── Kodo ──────────────────────────────────────────────────────────────────


class TestKodoNormalizer:
    def test_success_path(self):
        res = normalize_kodo({
            "exit_code": 0,
            "stdout": "patch applied",
            "files_changed": ["src/a.py"],
            "commands_run": ["pytest"],
        }, request_id="r-1", result_id="ers-1")
        assert res.ok is True
        assert res.status == ExecutionStatus.SUCCEEDED
        assert res.evidence.files_changed == ["src/a.py"]
        assert res.evidence.failure_reason is None

    def test_failure_extracts_reason_from_stderr(self):
        res = normalize_kodo({
            "exit_code": 1,
            "stderr": "tests failed\nAssertionError: x != y",
        })
        assert res.ok is False
        assert res.status == ExecutionStatus.FAILED
        assert res.evidence.failure_reason == "AssertionError: x != y"

    def test_unknown_keys_go_to_extensions(self):
        res = normalize_kodo({
            "exit_code": 0,
            "team_used": "_CLAUDE_FALLBACK_TEAM",
            "model_role": "worker_smart",
        })
        assert res.evidence.extensions["team_used"] == "_CLAUDE_FALLBACK_TEAM"
        assert res.evidence.extensions["model_role"] == "worker_smart"

    def test_serialized_result_passes_cxrp_schema(self):
        res = normalize_kodo({
            "exit_code": 0,
            "files_changed": ["a"],
            "team_used": "team_x",
        }, request_id="r", result_id="r1")
        validate_contract("execution_result", res.to_dict())

    def test_invalid_raw_raises(self):
        with pytest.raises(KodoNormErr):
            normalize_kodo("not a dict")
        with pytest.raises(KodoNormErr):
            normalize_kodo({"exit_code": "not-an-int"})


# ── Archon ────────────────────────────────────────────────────────────────


class TestArchonNormalizer:
    def test_success_outcome(self):
        res = normalize_archon({
            "outcome": "success",
            "output_text": "workflow completed",
            "workflow_events": [
                {"agent": "planner", "kind": "plan"},
                {"agent": "executor", "kind": "run"},
            ],
        })
        assert res.ok is True
        assert res.status == ExecutionStatus.SUCCEEDED
        summary = res.evidence.extensions["internal_trace_summary"]
        assert summary["step_count"] == 2
        assert summary["agents_used"] == ["executor", "planner"]
        assert summary["step_kinds"] == ["plan", "run"]

    def test_timeout_maps_to_timed_out(self):
        res = normalize_archon({"outcome": "timeout"})
        assert res.status == ExecutionStatus.TIMED_OUT
        assert res.ok is False

    def test_partial_maps_to_failed_with_reason(self):
        res = normalize_archon({
            "outcome": "partial",
            "error_text": "step 3 failed: timeout\nlast: connection_reset",
        })
        assert res.status == ExecutionStatus.FAILED
        assert res.evidence.failure_reason == "last: connection_reset"

    def test_internal_routing_flattened_not_leaked(self):
        """Per spec: 'no internal framework shapes leak past the adapter'.
        workflow_events go into internal_trace_summary under extensions,
        not as a top-level evidence field."""
        res = normalize_archon({
            "outcome": "success",
            "workflow_events": [{"agent": "x", "kind": "y"}],
        })
        # workflow_events is NOT a top-level Evidence field
        assert "workflow_events" not in {f.name for f in fields(Evidence)}
        # but it IS captured under extensions.internal_trace_summary
        assert "internal_trace_summary" in res.evidence.extensions

    def test_serialized_passes_cxrp_schema(self):
        res = normalize_archon({
            "outcome": "success",
            "workflow_events": [{"agent": "p", "kind": "k"}],
        }, request_id="r", result_id="r1")
        validate_contract("execution_result", res.to_dict())


# ── No-leakage invariant (the system-level rule) ──────────────────────────


class TestNoBackendShapeLeakage:
    """Phase 2's hard rule: no backend-specific shape leaks past the layer.

    The model-side invariant is enforced by both normalizers using only
    Evidence fields + extensions. The wire-side is enforced by the JSON
    schema (additionalProperties: false). This test catches anyone adding
    a field that escapes both gates.
    """

    def test_kodo_result_uses_only_contract_fields(self):
        res = normalize_kodo({"exit_code": 0, "vendor_field": "leaked"})
        # All top-level result fields are CxRP fields
        actual = {f.name for f in fields(res)}
        assert actual <= _CXRP_RESULT_FIELDS
        # Vendor data lives under extensions
        assert "vendor_field" in res.evidence.extensions

    def test_archon_result_uses_only_contract_fields(self):
        res = normalize_archon({"outcome": "success", "vendor_field": "leaked"})
        actual = {f.name for f in fields(res)}
        assert actual <= _CXRP_RESULT_FIELDS
        assert "vendor_field" in res.evidence.extensions

    def test_evidence_uses_only_contract_fields(self):
        res = normalize_kodo({"exit_code": 0})
        actual = {f.name for f in fields(res.evidence)}
        assert actual == _CXRP_EVIDENCE_FIELDS
