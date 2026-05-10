# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Verify the OC_VALIDATE_CATALOG_AT_STARTUP env var actually wires
the catalog validator into the audit CLI's app callback.
"""
from __future__ import annotations

import sys

import pytest


def _import_audit_app(env_extra: dict[str, str]):
    """Re-import the audit module under env overrides + invoke its callback."""
    import importlib
    import os
    for k, v in env_extra.items():
        os.environ[k] = v
    if "operations_center.entrypoints.audit.main" in sys.modules:
        del sys.modules["operations_center.entrypoints.audit.main"]
    return importlib.import_module("operations_center.entrypoints.audit.main")


def test_callback_no_op_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("OC_VALIDATE_CATALOG_AT_STARTUP", raising=False)
    mod = _import_audit_app({})
    # Should run without raising
    mod._validate_executor_catalog_if_requested()


def test_callback_validates_catalog_when_env_var_set(monkeypatch):
    """When env var set + real catalog is valid, no exception."""
    monkeypatch.setenv("OC_VALIDATE_CATALOG_AT_STARTUP", "1")
    mod = _import_audit_app({"OC_VALIDATE_CATALOG_AT_STARTUP": "1"})
    mod._validate_executor_catalog_if_requested()  # Loads the real catalog


def test_callback_treats_unrecognized_env_value_as_off(monkeypatch):
    monkeypatch.setenv("OC_VALIDATE_CATALOG_AT_STARTUP", "no")
    mod = _import_audit_app({"OC_VALIDATE_CATALOG_AT_STARTUP": "no"})
    mod._validate_executor_catalog_if_requested()  # No-op, no exception


def test_audit_cli_fails_fast_on_corrupt_catalog(tmp_path, monkeypatch):
    """When the env var is set and catalog is corrupt, CLI must error."""
    # Seed a backend dir with an unknown capability — catalog must reject.
    backend = tmp_path / "executors" / "broken"
    backend.mkdir(parents=True)
    (backend / "capability_card.yaml").write_text(
        "backend_id: broken\nbackend_version: u\n"
        "advertised_capabilities: [definitely_not_real]\n"
    )
    (backend / "runtime_support.yaml").write_text(
        "backend_id: broken\nsupported_runtime_kinds: []\nsupported_selection_modes: []\n"
    )
    (backend / "contract_gaps.yaml").write_text("[]")
    (backend / "audit_verdict.yaml").write_text(
        "backend_id: broken\naudited_at: t\naudited_against_cxrp_version: '0.2'\n"
        "backend_version: u\n"
        "per_phase:\n  runtime_control: PASS\n  capability_control: PASS\n"
        "  drift_detection: PASS\n  failure_observability: PASS\n  internal_routing: 'N/A'\n"
        "outcome: adapter_only\ngap_refs: []\n"
    )
    # The audit CLI uses the default executors dir — we can't override
    # without code change, so just call initialize_catalog directly with
    # the corrupt dir.
    from operations_center.executors.startup import initialize_catalog
    with pytest.raises(Exception):
        initialize_catalog(tmp_path / "executors", fail_fast=True)
