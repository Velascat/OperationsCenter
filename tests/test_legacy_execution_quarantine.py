from __future__ import annotations

import importlib

import pytest


def test_legacy_execution_package_no_longer_exports_service() -> None:
    legacy_execution = importlib.import_module("control_plane.legacy_execution")
    assert not hasattr(legacy_execution, "ExecutionService")


def test_legacy_execution_service_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTROL_PLANE_ENABLE_LEGACY_EXECUTION", raising=False)

    from control_plane.config.settings import Settings
    from control_plane.legacy_execution.service import ExecutionService

    settings = Settings.model_validate(
        {
            "plane": {
                "base_url": "http://plane.local",
                "api_token_env": "PLANE_API_TOKEN",
                "workspace_slug": "ws",
                "project_id": "proj",
            },
            "git": {"provider": "github"},
            "kodo": {},
            "repos": {},
        }
    )

    with pytest.raises(RuntimeError, match="compatibility-only and disabled by default"):
        ExecutionService(settings)
