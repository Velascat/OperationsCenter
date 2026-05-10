# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the operations-center-archon-probe entrypoint."""
from __future__ import annotations

from typer.testing import CliRunner

from operations_center.backends.archon.http_client import (
    HealthProbeResult,
    WorkflowSummary,
)
from operations_center.entrypoints.archon_probe.main import app


runner = CliRunner()


class TestHealthProbeCommand:
    def test_healthy_exits_zero(self, monkeypatch):
        def _ok(base_url, **_kw):
            return HealthProbeResult(
                ok=True, base_url=base_url, status_code=200, summary="archon healthy",
            )

        monkeypatch.setattr(
            "operations_center.backends.archon.archon_health_probe", _ok,
        )
        result = runner.invoke(app, ["--base-url", "http://x"])
        assert result.exit_code == 0
        assert "[OK]" in result.stdout
        assert "healthy" in result.stdout

    def test_unreachable_exits_one(self, monkeypatch):
        def _unreachable(base_url, **_kw):
            return HealthProbeResult(
                ok=False, base_url=base_url, status_code=None,
                summary="connect error: refused",
            )

        monkeypatch.setattr(
            "operations_center.backends.archon.archon_health_probe", _unreachable,
        )
        result = runner.invoke(app, ["--base-url", "http://x"])
        assert result.exit_code == 1
        # Error output goes to stderr in typer.
        assert "[UNREACHABLE]" in (result.stdout + result.stderr)


class TestListWorkflows:
    def test_lists_workflows(self, monkeypatch):
        def _list(base_url, **_kw):
            return [
                WorkflowSummary(name="archon-assist", description="Generic goal"),
                WorkflowSummary(name="archon-fix-pr", description="Fix a PR"),
            ]

        monkeypatch.setattr(
            "operations_center.backends.archon.http_client.archon_list_workflows",
            _list,
        )
        result = runner.invoke(app, ["--list-workflows"])
        assert result.exit_code == 0
        assert "archon-assist" in result.stdout
        assert "archon-fix-pr" in result.stdout

    def test_empty_list_exits_one(self, monkeypatch):
        monkeypatch.setattr(
            "operations_center.backends.archon.http_client.archon_list_workflows",
            lambda *a, **k: [],
        )
        result = runner.invoke(app, ["--list-workflows"])
        assert result.exit_code == 1
        assert "no workflows" in (result.stdout + result.stderr).lower()
