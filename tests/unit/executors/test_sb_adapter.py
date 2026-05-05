# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""R4 — OC's catalog adapter satisfies the SB ExecutorCatalog port."""
from __future__ import annotations

from pathlib import Path

from operations_center.executors.catalog import load_catalog
from operations_center.executors.catalog.sb_adapter import SwitchboardCatalogAdapter

_REAL_DIR = Path("src/operations_center/executors")


def test_sb_adapter_runtime_query_round_trip():
    cat = load_catalog(_REAL_DIR)
    sb_cat = SwitchboardCatalogAdapter(cat)
    assert "kodo" in sb_cat.backends_supporting_runtime(runtime_kind="cli_subscription")


def test_sb_adapter_capability_query():
    cat = load_catalog(_REAL_DIR)
    sb_cat = SwitchboardCatalogAdapter(cat)
    out = sb_cat.backends_supporting_capabilities(required_capabilities={"repo_read"})
    assert {"kodo", "archon"}.issubset(set(out))


def test_sb_adapter_outcome_query():
    cat = load_catalog(_REAL_DIR)
    sb_cat = SwitchboardCatalogAdapter(cat)
    # Post-spike: archon also adapter_plus_wrapper, no patch pending
    assert sb_cat.backends_by_outcome(outcome="adapter_plus_wrapper") == ["archon", "kodo"]
    assert sb_cat.backends_by_outcome(outcome="upstream_patch_pending") == []


def test_sb_adapter_satisfies_protocol():
    """isinstance check via SB's runtime-checkable Protocol — proves the
    OC adapter implements every method SB depends on."""
    try:
        from switchboard.ports.executor_catalog import ExecutorCatalog as SbCatalog
    except ImportError:
        import pytest
        pytest.skip("SwitchBoard not installed in OC's venv")
    cat = load_catalog(_REAL_DIR)
    sb_cat = SwitchboardCatalogAdapter(cat)
    assert isinstance(sb_cat, SbCatalog)
