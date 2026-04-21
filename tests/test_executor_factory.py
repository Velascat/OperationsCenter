# tests/test_executor_factory.py
"""Unit tests for ExecutorFactory — executor selection and creation."""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from control_plane.adapters.executor.aider import AiderAdapter
from control_plane.adapters.executor.factory import ExecutorFactory
from control_plane.adapters.executor.kodo import KodoExecutorAdapter
from control_plane.config.settings import AiderSettings, KodoSettings, RepoSettings, Settings


def _minimal_settings(
    executor: str = "kodo",
    switchboard_url: str = "",
) -> Settings:
    """Build a minimal Settings instance for testing."""
    raw = {
        "plane": {
            "base_url": "http://localhost:8080",
            "api_token_env": "PLANE_API_TOKEN",
            "workspace_slug": "test",
            "project_id": "test-uuid",
        },
        "git": {"provider": "github"},
        "kodo": {"binary": "kodo"},
        "repos": {
            "TestRepo": {
                "clone_url": "git@github.com:test/repo.git",
                "default_branch": "main",
                "executor": executor,
            }
        },
    }
    if switchboard_url:
        raw["spec_director"] = {"switchboard_url": switchboard_url}
    return Settings.model_validate(raw)


# ---------------------------------------------------------------------------
# Factory creates correct adapter type
# ---------------------------------------------------------------------------


def test_create_kodo_returns_kodo_executor_adapter(monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_URL", raising=False)
    settings = _minimal_settings()
    adapter = ExecutorFactory.create("kodo", settings)
    assert isinstance(adapter, KodoExecutorAdapter)
    assert adapter.name() == "kodo"


def test_create_aider_returns_aider_adapter(monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_URL", raising=False)
    settings = _minimal_settings()
    adapter = ExecutorFactory.create("aider", settings)
    assert isinstance(adapter, AiderAdapter)
    assert adapter.name() == "aider"


def test_create_case_insensitive(monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_URL", raising=False)
    settings = _minimal_settings()
    assert isinstance(ExecutorFactory.create("Kodo", settings), KodoExecutorAdapter)
    assert isinstance(ExecutorFactory.create("AIDER", settings), AiderAdapter)


def test_create_unknown_raises_value_error(monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_URL", raising=False)
    settings = _minimal_settings()
    with pytest.raises(ValueError, match="Unknown executor type"):
        ExecutorFactory.create("codex", settings)


# ---------------------------------------------------------------------------
# for_repo() uses per-repo executor field
# ---------------------------------------------------------------------------


def test_for_repo_uses_repo_executor_field(monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_URL", raising=False)
    settings = _minimal_settings(executor="aider")
    adapter = ExecutorFactory.for_repo("TestRepo", settings)
    assert isinstance(adapter, AiderAdapter)


def test_for_repo_defaults_to_kodo_when_no_executor_field(monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_URL", raising=False)
    settings = _minimal_settings(executor="kodo")
    adapter = ExecutorFactory.for_repo("TestRepo", settings)
    assert isinstance(adapter, KodoExecutorAdapter)


def test_for_repo_unknown_key_falls_back_to_kodo(monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_URL", raising=False)
    settings = _minimal_settings()
    adapter = ExecutorFactory.for_repo("NonExistentRepo", settings)
    assert isinstance(adapter, KodoExecutorAdapter)


# ---------------------------------------------------------------------------
# SwitchBoard URL resolution
# ---------------------------------------------------------------------------


def test_switchboard_url_from_env(monkeypatch):
    monkeypatch.setenv("SWITCHBOARD_URL", "http://sb-env:20401")
    settings = _minimal_settings()
    adapter = ExecutorFactory.create("kodo", settings)
    assert isinstance(adapter, KodoExecutorAdapter)
    assert adapter._switchboard_url == "http://sb-env:20401"


def test_switchboard_url_from_settings_when_no_env(monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_URL", raising=False)
    settings = _minimal_settings(switchboard_url="http://sb-cfg:20401")
    adapter = ExecutorFactory.create("kodo", settings)
    assert adapter._switchboard_url == "http://sb-cfg:20401"


def test_env_takes_precedence_over_settings(monkeypatch):
    monkeypatch.setenv("SWITCHBOARD_URL", "http://sb-env:20401")
    settings = _minimal_settings(switchboard_url="http://sb-cfg:20401")
    adapter = ExecutorFactory.create("kodo", settings)
    assert adapter._switchboard_url == "http://sb-env:20401"


def test_aider_adapter_receives_switchboard_url(monkeypatch):
    monkeypatch.setenv("SWITCHBOARD_URL", "http://sb:20401")
    settings = _minimal_settings()
    adapter = ExecutorFactory.create("aider", settings)
    assert isinstance(adapter, AiderAdapter)
    assert adapter._switchboard_url == "http://sb:20401"


# ---------------------------------------------------------------------------
# Both adapters satisfy Executor protocol
# ---------------------------------------------------------------------------


def test_both_adapters_satisfy_executor_protocol(monkeypatch):
    from control_plane.adapters.executor.protocol import Executor

    monkeypatch.delenv("SWITCHBOARD_URL", raising=False)
    settings = _minimal_settings()
    assert isinstance(ExecutorFactory.create("kodo", settings), Executor)
    assert isinstance(ExecutorFactory.create("aider", settings), Executor)
