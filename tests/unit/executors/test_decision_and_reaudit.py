# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the Phase 11 decision matrix + Phase 13 re-audit triggers."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from operations_center.executors._artifacts import (
    AuditOutcome,
    AuditVerdict,
    PhaseClassification,
)
from operations_center.executors.catalog import load_catalog
from operations_center.executors.catalog.loader import CatalogValidationError
from operations_center.executors.decision import (
    ExpectedOutcome,
    compute_expected_outcome,
    verdict_is_consistent,
)
from operations_center.executors.reaudit import (
    ReauditReason,
    needs_reaudit,
)


# ── Phase 11 decision matrix ────────────────────────────────────────────


class TestDecisionMatrix:
    def _phases(self, **overrides) -> dict[str, PhaseClassification]:
        base = {
            "runtime_control": PhaseClassification.PASS,
            "capability_control": PhaseClassification.PASS,
            "drift_detection": PhaseClassification.PASS,
            "failure_observability": PhaseClassification.PASS,
            "internal_routing": PhaseClassification.NA,
        }
        for k, v in overrides.items():
            base[k] = v
        return base

    def test_all_pass_na_is_adapter_only(self):
        d = compute_expected_outcome(self._phases())
        assert d.expected == ExpectedOutcome.ADAPTER_ONLY

    def test_any_partial_is_adapter_plus_wrapper(self):
        d = compute_expected_outcome(self._phases(runtime_control=PhaseClassification.PARTIAL))
        assert d.expected == ExpectedOutcome.ADAPTER_PLUS_WRAPPER
        assert d.partial_phases == ("runtime_control",)

    def test_any_fail_is_upstream_or_fork(self):
        d = compute_expected_outcome(self._phases(runtime_control=PhaseClassification.FAIL))
        assert d.expected == ExpectedOutcome.UPSTREAM_PATCH_PENDING_OR_FORK

    def test_partial_and_fail_picks_fail(self):
        """FAIL dominates PARTIAL — outcome is determined by the worst phase."""
        d = compute_expected_outcome(self._phases(
            runtime_control=PhaseClassification.PARTIAL,
            capability_control=PhaseClassification.FAIL,
        ))
        assert d.expected == ExpectedOutcome.UPSTREAM_PATCH_PENDING_OR_FORK


class TestVerdictConsistency:
    def _verdict(self, *, outcome: AuditOutcome, **phase_overrides) -> AuditVerdict:
        per_phase = {
            "runtime_control": PhaseClassification.PASS,
            "capability_control": PhaseClassification.PASS,
            "drift_detection": PhaseClassification.PASS,
            "failure_observability": PhaseClassification.PASS,
            "internal_routing": PhaseClassification.NA,
        }
        for k, v in phase_overrides.items():
            per_phase[k] = v
        return AuditVerdict(
            backend_id="x", audited_at="2026-05-05",
            audited_against_cxrp_version="0.2", backend_version="u",
            per_phase=per_phase, outcome=outcome, gap_refs=[],
        )

    def test_adapter_only_with_all_pass_consistent(self):
        ok, _ = verdict_is_consistent(self._verdict(outcome=AuditOutcome.ADAPTER_ONLY))
        assert ok

    def test_adapter_only_with_partial_inconsistent(self):
        ok, reason = verdict_is_consistent(self._verdict(
            outcome=AuditOutcome.ADAPTER_ONLY,
            runtime_control=PhaseClassification.PARTIAL,
        ))
        assert not ok and "adapter_plus_wrapper" in reason

    def test_fork_with_fail_consistent(self):
        ok, _ = verdict_is_consistent(self._verdict(
            outcome=AuditOutcome.FORK_REQUIRED,
            runtime_control=PhaseClassification.FAIL,
        ))
        assert ok

    def test_upstream_patch_with_fail_consistent(self):
        ok, _ = verdict_is_consistent(self._verdict(
            outcome=AuditOutcome.UPSTREAM_PATCH_PENDING,
            runtime_control=PhaseClassification.FAIL,
        ))
        assert ok


# ── Phase 12 enforcement: matrix consistency at catalog load ────────────


def _seed_inconsistent_backend(base: Path) -> None:
    backend = base / "x"
    backend.mkdir()
    (backend / "capability_card.yaml").write_text(
        "backend_id: x\nbackend_version: u\nadvertised_capabilities: []\n"
    )
    (backend / "runtime_support.yaml").write_text(
        "backend_id: x\nbackend_version: u\n"
        "supported_runtime_kinds: []\nsupported_selection_modes: []\n"
    )
    (backend / "contract_gaps.yaml").write_text("[]")
    # All PASS but outcome is fork_required → matrix inconsistent
    (backend / "audit_verdict.yaml").write_text(
        "backend_id: x\naudited_at: t\naudited_against_cxrp_version: '0.2'\n"
        "backend_version: u\n"
        "per_phase:\n  runtime_control: PASS\n  capability_control: PASS\n"
        "  drift_detection: PASS\n  failure_observability: PASS\n  internal_routing: PASS\n"
        "outcome: fork_required\ngap_refs: []\n"
    )


def test_catalog_rejects_matrix_inconsistent_verdict(tmp_path):
    _seed_inconsistent_backend(tmp_path)
    with pytest.raises(CatalogValidationError, match="expected upstream_patch_pending or fork_required|expected adapter_only"):
        load_catalog(tmp_path)


# ── Phase 13 re-audit triggers ──────────────────────────────────────────


def _verdict(audited_at="2026-05-05", backend_version="2.0", cxrp="0.2") -> AuditVerdict:
    return AuditVerdict(
        backend_id="x", audited_at=audited_at,
        audited_against_cxrp_version=cxrp, backend_version=backend_version,
        per_phase={
            "runtime_control": PhaseClassification.PASS,
            "capability_control": PhaseClassification.PASS,
            "drift_detection": PhaseClassification.PASS,
            "failure_observability": PhaseClassification.PASS,
            "internal_routing": PhaseClassification.NA,
        },
        outcome=AuditOutcome.ADAPTER_ONLY, gap_refs=[],
    )


class TestReaudit:
    def test_no_triggers_returns_not_needed(self):
        d = needs_reaudit(
            _verdict(),
            current_backend_version="2.0",
            current_cxrp_version="0.2",
            runtimebinding_schema_changed=False,
            capabilityset_schema_changed=False,
            today=date(2026, 5, 5),
        )
        assert not d.needed
        assert d.reasons == ()

    def test_backend_version_change_triggers(self):
        d = needs_reaudit(
            _verdict(backend_version="2.0"),
            current_backend_version="3.0",
            current_cxrp_version="0.2",
            runtimebinding_schema_changed=False,
            capabilityset_schema_changed=False,
        )
        assert d.needed
        assert ReauditReason.BACKEND_VERSION_CHANGED in d.reasons

    def test_runtimebinding_schema_change_triggers(self):
        d = needs_reaudit(
            _verdict(),
            current_backend_version="2.0",
            current_cxrp_version="0.2",
            runtimebinding_schema_changed=True,
            capabilityset_schema_changed=False,
        )
        assert ReauditReason.RUNTIMEBINDING_SCHEMA_CHANGED in d.reasons

    def test_cxrp_minor_version_advance_triggers(self):
        d = needs_reaudit(
            _verdict(cxrp="0.2"),
            current_backend_version="2.0",
            current_cxrp_version="0.3",
            runtimebinding_schema_changed=False,
            capabilityset_schema_changed=False,
        )
        assert ReauditReason.CXRP_MINOR_VERSION_ADVANCED in d.reasons

    def test_stale_and_recently_invoked_triggers(self):
        # audited 100 days ago, invoked 5 days ago
        today = date(2026, 5, 5)
        d = needs_reaudit(
            _verdict(audited_at=(today - timedelta(days=100)).isoformat()),
            current_backend_version="2.0",
            current_cxrp_version="0.2",
            runtimebinding_schema_changed=False,
            capabilityset_schema_changed=False,
            last_invoked_at=today - timedelta(days=5),
            today=today,
        )
        assert ReauditReason.STALE_AND_RECENTLY_INVOKED in d.reasons

    def test_stale_but_dormant_does_not_trigger_staleness(self):
        # audited 100 days ago, last invoked 60 days ago — dormant
        today = date(2026, 5, 5)
        d = needs_reaudit(
            _verdict(audited_at=(today - timedelta(days=100)).isoformat()),
            current_backend_version="2.0",
            current_cxrp_version="0.2",
            runtimebinding_schema_changed=False,
            capabilityset_schema_changed=False,
            last_invoked_at=today - timedelta(days=60),
            today=today,
        )
        assert ReauditReason.STALE_AND_RECENTLY_INVOKED not in d.reasons
