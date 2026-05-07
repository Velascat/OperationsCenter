# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""R5 — startup hook + re-audit CLI tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from operations_center.executors.startup import initialize_catalog


_REAL_DIR = Path("src/operations_center/executors")


class TestStartupHook:
    def test_initialize_loads_real_catalog(self):
        cat = initialize_catalog(_REAL_DIR)
        assert cat is not None
        assert "kodo" in cat.entries
        assert "archon" in cat.entries

    def test_fail_fast_raises_on_invalid_dir(self, tmp_path):
        backend = tmp_path / "x"
        backend.mkdir()
        # Missing all 4 required artifacts → catalog skips it, returns empty.
        cat = initialize_catalog(tmp_path, fail_fast=True)
        assert cat is not None and cat.entries == {}

    def test_fail_fast_false_swallows_errors(self, tmp_path):
        bad = tmp_path / "x"
        bad.mkdir()
        (bad / "capability_card.yaml").write_text("backend_id: x\nadvertised_capabilities: [made_up_cap]\n")
        (bad / "runtime_support.yaml").write_text("backend_id: x\nsupported_runtime_kinds: []\nsupported_selection_modes: []\n")
        (bad / "contract_gaps.yaml").write_text("[]")
        (bad / "audit_verdict.yaml").write_text(
            "backend_id: x\naudited_at: t\naudited_against_cxrp_version: '0.2'\n"
            "backend_version: u\nper_phase:\n  runtime_control: PASS\n"
            "  capability_control: PASS\n  drift_detection: PASS\n"
            "  failure_observability: PASS\n  internal_routing: 'N/A'\n"
            "outcome: adapter_only\ngap_refs: []\n"
        )
        cat = initialize_catalog(tmp_path, fail_fast=False)
        assert cat is None  # validation failed, swallowed

        with pytest.raises(Exception):
            initialize_catalog(tmp_path, fail_fast=True)


class TestReauditCLI:
    def test_cli_returns_zero_for_no_triggers(self):
        proc = subprocess.run(
            [sys.executable, "-m", "operations_center.entrypoints.reaudit_check.main",
             "--dir", str(_REAL_DIR), "--json"],
            capture_output=True, text=True,
        )
        report = json.loads(proc.stdout)
        # All current verdicts are dated 2026-05-05; no triggers fire today
        assert all(not info["needed"] for info in report["backends"].values())
        assert proc.returncode == 0

    def test_cli_returns_nonzero_when_runtimebinding_changes(self):
        proc = subprocess.run(
            [sys.executable, "-m", "operations_center.entrypoints.reaudit_check.main",
             "--dir", str(_REAL_DIR), "--runtimebinding-changed", "--json"],
            capture_output=True, text=True,
        )
        report = json.loads(proc.stdout)
        # Every backend should now need re-audit
        assert all(info["needed"] for info in report["backends"].values())
        assert proc.returncode == 1
