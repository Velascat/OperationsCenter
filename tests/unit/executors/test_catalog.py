# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Catalog v1 tests — load + the three queries + enforcement rules."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from operations_center.executors._artifacts import AuditOutcome
from operations_center.executors.catalog import (
    backends_by_outcome,
    backends_supporting_capabilities,
    backends_supporting_runtime,
    load_catalog,
)
from operations_center.executors.catalog.loader import CatalogValidationError


_REAL_DIR = Path("src/operations_center/executors")


# ── load against the shipped artifacts ──────────────────────────────────


class TestRealCatalog:
    def test_loads_kodo_and_archon(self):
        cat = load_catalog(_REAL_DIR)
        assert "kodo" in cat.entries
        assert "archon" in cat.entries

    def test_kodo_entry_well_formed(self):
        cat = load_catalog(_REAL_DIR)
        kodo = cat.get("kodo")
        assert kodo is not None
        assert kodo.audit_verdict.outcome == AuditOutcome.ADAPTER_PLUS_WRAPPER
        assert kodo.capability_card.backend_id == "kodo"

    def test_archon_entry_well_formed(self):
        cat = load_catalog(_REAL_DIR)
        archon = cat.get("archon")
        assert archon is not None
        assert archon.audit_verdict.outcome == AuditOutcome.UPSTREAM_PATCH_PENDING


# ── Query 1: runtime support ────────────────────────────────────────────


class TestRuntimeQuery:
    def test_finds_kodo_for_cli_subscription(self):
        cat = load_catalog(_REAL_DIR)
        out = backends_supporting_runtime(cat, runtime_kind="cli_subscription")
        assert "kodo" in out
        # Archon's runtime_support is empty until G-001 closes
        assert "archon" not in out

    def test_returns_empty_for_unsupported_kind(self):
        cat = load_catalog(_REAL_DIR)
        assert backends_supporting_runtime(cat, runtime_kind="human") == []


# ── Query 2: capability match ───────────────────────────────────────────


class TestCapabilityQuery:
    def test_finds_both_for_repo_read(self):
        cat = load_catalog(_REAL_DIR)
        out = backends_supporting_capabilities(cat, required_capabilities={"repo_read"})
        assert set(out) == {"kodo", "archon"}

    def test_finds_only_kodo_for_shell_write(self):
        cat = load_catalog(_REAL_DIR)
        out = backends_supporting_capabilities(cat, required_capabilities={"shell_write"})
        assert out == ["kodo"]

    def test_returns_empty_for_impossible_combo(self):
        cat = load_catalog(_REAL_DIR)
        out = backends_supporting_capabilities(
            cat, required_capabilities={"repo_patch", "human_review"},
        )
        assert out == []


# ── Query 3: verdict lookup ─────────────────────────────────────────────


class TestVerdictQuery:
    def test_adapter_plus_wrapper(self):
        cat = load_catalog(_REAL_DIR)
        assert backends_by_outcome(cat, outcome="adapter_plus_wrapper") == ["kodo"]

    def test_upstream_patch_pending(self):
        cat = load_catalog(_REAL_DIR)
        assert backends_by_outcome(cat, outcome=AuditOutcome.UPSTREAM_PATCH_PENDING) == ["archon"]

    def test_no_forks_yet(self):
        cat = load_catalog(_REAL_DIR)
        assert backends_by_outcome(cat, outcome="fork_required") == []


# ── Enforcement rules ───────────────────────────────────────────────────


def _seed_minimal_backend(base: Path, backend_id: str, *,
                          outcome: str = "adapter_only",
                          gaps_yaml: str = "[]",
                          gap_refs: list[str] | None = None) -> None:
    # Pick a per_phase set that's matrix-consistent with the outcome,
    # so this fixture isolates the gap-ref enforcement under test.
    if outcome in ("fork_required", "upstream_patch_pending"):
        runtime_phase = "FAIL"
    elif outcome == "adapter_plus_wrapper":
        runtime_phase = "PARTIAL"
    else:
        runtime_phase = "PASS"
    backend = base / backend_id
    backend.mkdir(parents=True)
    (backend / "capability_card.yaml").write_text(
        f"backend_id: {backend_id}\nbackend_version: u\n"
        "advertised_capabilities: [repo_read]\n"
    )
    (backend / "runtime_support.yaml").write_text(
        f"backend_id: {backend_id}\nbackend_version: u\n"
        "supported_runtime_kinds: []\nsupported_selection_modes: []\n"
    )
    (backend / "contract_gaps.yaml").write_text(gaps_yaml)
    refs_yaml = "gap_refs: []" if not gap_refs else "gap_refs:\n" + "\n".join(f"  - {r}" for r in gap_refs)
    (backend / "audit_verdict.yaml").write_text(
        f"backend_id: {backend_id}\naudited_at: t\n"
        f"audited_against_cxrp_version: '0.2'\nbackend_version: u\n"
        f"per_phase:\n  runtime_control: {runtime_phase}\n  capability_control: PASS\n"
        f"  drift_detection: PASS\n  failure_observability: PASS\n  internal_routing: 'N/A'\n"
        f"outcome: {outcome}\n{refs_yaml}\n"
    )


class TestEnforcement:
    def test_unresolved_gap_ref_rejected(self, tmp_path):
        _seed_minimal_backend(tmp_path, "x", gap_refs=["G-NOT-THERE"])
        with pytest.raises(CatalogValidationError, match="not found"):
            load_catalog(tmp_path)

    def test_fork_required_without_forked_gap_rejected(self, tmp_path):
        _seed_minimal_backend(
            tmp_path, "x",
            outcome="fork_required",
            gaps_yaml=(
                "- id: G-1\n  gap: g\n  discovered_at: t\n  impact: i\n"
                "  workaround: w\n  fork_threshold: f\n  status: open\n"
            ),
            gap_refs=["G-1"],
        )
        with pytest.raises(CatalogValidationError, match="status: forked"):
            load_catalog(tmp_path)

    def test_fork_required_with_forked_gap_loads(self, tmp_path):
        _seed_minimal_backend(
            tmp_path, "x",
            outcome="fork_required",
            gaps_yaml=(
                "- id: G-1\n  gap: g\n  discovered_at: t\n  impact: i\n"
                "  workaround: w\n  fork_threshold: f\n  status: forked\n"
            ),
            gap_refs=["G-1"],
        )
        cat = load_catalog(tmp_path)
        assert "x" in cat.entries

    def test_upstream_patch_pending_without_deadline_rejected(self, tmp_path):
        _seed_minimal_backend(
            tmp_path, "x",
            outcome="upstream_patch_pending",
            gaps_yaml=(
                "- id: G-1\n  gap: g\n  discovered_at: t\n  impact: i\n"
                "  workaround: w\n  fork_threshold: f\n  status: open\n"
            ),
            gap_refs=["G-1"],
        )
        with pytest.raises(CatalogValidationError, match="patch_deadline"):
            load_catalog(tmp_path)

    def test_upstream_patch_pending_with_deadline_loads(self, tmp_path):
        _seed_minimal_backend(
            tmp_path, "x",
            outcome="upstream_patch_pending",
            gaps_yaml=(
                "- id: G-1\n  gap: g\n  discovered_at: t\n  impact: i\n"
                "  workaround: w\n  fork_threshold: f\n  status: open\n"
                "  patch_deadline: '2026-12-31'\n"
            ),
            gap_refs=["G-1"],
        )
        cat = load_catalog(tmp_path)
        assert "x" in cat.entries
