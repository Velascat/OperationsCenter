# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_executor_stack_removed_from_source_tree() -> None:
    assert not (REPO_ROOT / "src" / "operations_center" / "adapters" / "executor").exists()


def test_legacy_execution_removed_from_source_tree() -> None:
    assert not (REPO_ROOT / "src" / "operations_center" / "legacy_execution").exists()


def test_removed_modules_cannot_be_imported() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("operations_center.adapters.executor.factory")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("operations_center.legacy_execution")


def test_routing_client_no_longer_supports_in_process_bypass() -> None:
    source = (REPO_ROOT / "src" / "operations_center" / "routing" / "client.py").read_text(encoding="utf-8")
    assert "LocalLaneRoutingClient" not in source
    assert 'os.environ.get("SWITCHBOARD_URL")' not in source


def test_no_execution_proxy_env_injection_in_canonical_backends() -> None:
    paths = [
        REPO_ROOT / "src" / "operations_center" / "backends" / "kodo" / "invoke.py",
        REPO_ROOT / "src" / "operations_center" / "backends" / "archon" / "invoke.py",
        REPO_ROOT / "src" / "operations_center" / "backends" / "direct_local" / "adapter.py",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "OPENAI_API_BASE" not in source
