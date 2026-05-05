# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the per-backend artifact loaders + validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.executors._artifacts import (
    AuditArtifactError,
    AuditOutcome,
    GapStatus,
    PhaseClassification,
    load_audit_verdict,
    load_capability_card,
    load_contract_gaps,
    load_runtime_support,
)


_KODO_DIR   = Path("src/operations_center/executors/kodo")
_ARCHON_DIR = Path("src/operations_center/executors/archon")


# ── shipped artifacts load and validate ─────────────────────────────────


class TestShippedArtifacts:
    def test_kodo_contract_gaps(self):
        gaps = load_contract_gaps(_KODO_DIR / "contract_gaps.yaml")
        assert any(g.id == "G-001" for g in gaps)
        assert all(isinstance(g.status, GapStatus) for g in gaps)

    def test_kodo_capability_card(self):
        card = load_capability_card(_KODO_DIR / "capability_card.yaml")
        assert card.backend_id == "kodo"
        assert "repo_patch" in card.advertised_capabilities

    def test_kodo_runtime_support(self):
        rs = load_runtime_support(_KODO_DIR / "runtime_support.yaml")
        assert "cli_subscription" in rs.supported_runtime_kinds

    def test_kodo_verdict(self):
        v = load_audit_verdict(_KODO_DIR / "audit_verdict.yaml")
        assert v.outcome == AuditOutcome.ADAPTER_PLUS_WRAPPER
        assert v.per_phase["internal_routing"] == PhaseClassification.NA
        assert "G-001" in v.gap_refs

    def test_archon_contract_gaps(self):
        gaps = load_contract_gaps(_ARCHON_DIR / "contract_gaps.yaml")
        assert any(g.id == "G-001" and g.patch_deadline for g in gaps)

    def test_archon_capability_card(self):
        card = load_capability_card(_ARCHON_DIR / "capability_card.yaml")
        assert card.backend_id == "archon"

    def test_archon_runtime_support(self):
        rs = load_runtime_support(_ARCHON_DIR / "runtime_support.yaml")
        # Archon currently supports nothing — adapter is transport-shaped
        assert rs.supported_runtime_kinds == []

    def test_archon_verdict(self):
        v = load_audit_verdict(_ARCHON_DIR / "audit_verdict.yaml")
        assert v.outcome == AuditOutcome.UPSTREAM_PATCH_PENDING


# ── validation rejects bad data ─────────────────────────────────────────


class TestValidation:
    def test_capability_card_rejects_subjective_fields(self, tmp_path):
        p = tmp_path / "cap.yaml"
        p.write_text("backend_id: x\nadvertised_capabilities: []\ngood_for: [x]\n")
        with pytest.raises(AuditArtifactError, match="recommendations.md"):
            load_capability_card(p)

    def test_capability_card_rejects_unknown_capability(self, tmp_path):
        p = tmp_path / "cap.yaml"
        p.write_text("backend_id: x\nadvertised_capabilities: [made_up_cap]\n")
        with pytest.raises(AuditArtifactError, match="unknown CapabilitySet"):
            load_capability_card(p)

    def test_runtime_support_rejects_unknown_kind(self, tmp_path):
        p = tmp_path / "rs.yaml"
        p.write_text("backend_id: x\nsupported_runtime_kinds: [made_up_kind]\n")
        with pytest.raises(AuditArtifactError, match="unknown RuntimeKind"):
            load_runtime_support(p)

    def test_runtime_support_rejects_unknown_selection_mode(self, tmp_path):
        p = tmp_path / "rs.yaml"
        p.write_text(
            "backend_id: x\nsupported_runtime_kinds: []\n"
            "supported_selection_modes: [vibes]\n"
        )
        with pytest.raises(AuditArtifactError, match="unknown SelectionMode"):
            load_runtime_support(p)

    def test_verdict_rejects_missing_phase(self, tmp_path):
        p = tmp_path / "v.yaml"
        p.write_text(
            "backend_id: x\naudited_at: t\naudited_against_cxrp_version: '0.2'\n"
            "backend_version: u\noutcome: adapter_only\n"
            "per_phase:\n  runtime_control: PASS\n"
        )
        with pytest.raises(AuditArtifactError, match="missing required phase"):
            load_audit_verdict(p)

    def test_verdict_rejects_invalid_phase_value(self, tmp_path):
        p = tmp_path / "v.yaml"
        p.write_text(
            "backend_id: x\naudited_at: t\naudited_against_cxrp_version: '0.2'\n"
            "backend_version: u\noutcome: adapter_only\n"
            "per_phase:\n"
            "  runtime_control: GREATEST\n"
            "  capability_control: PASS\n"
            "  drift_detection: PASS\n"
            "  failure_observability: PASS\n"
            "  internal_routing: 'N/A'\n"
        )
        with pytest.raises(AuditArtifactError, match="invalid value"):
            load_audit_verdict(p)

    def test_gap_rejects_invalid_status(self, tmp_path):
        p = tmp_path / "g.yaml"
        p.write_text(
            "- id: g\n  gap: x\n  discovered_at: t\n  impact: i\n"
            "  workaround: w\n  fork_threshold: f\n  status: zombie\n"
        )
        with pytest.raises(AuditArtifactError, match="invalid status"):
            load_contract_gaps(p)
