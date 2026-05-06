# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the archon HTTP client (health probe + concrete adapter)."""
from __future__ import annotations

import httpx

from operations_center.backends.archon.http_client import (
    HealthProbeResult,
    archon_health_probe,
)
from operations_center.backends.archon.invoke import (
    ArchonWorkflowConfig,
    HttpArchonAdapter,
)
from pathlib import Path


def _stub_client(*, response: httpx.Response | None = None,
                 raises: Exception | None = None) -> httpx.Client:
    def _handler(request: httpx.Request) -> httpx.Response:
        if raises is not None:
            raise raises
        return response or httpx.Response(200, json={"status": "ok"})

    return httpx.Client(transport=httpx.MockTransport(_handler))


class TestHealthProbe:
    def test_2xx_response_is_healthy(self):
        client = _stub_client(response=httpx.Response(200, text="ok"))
        result = archon_health_probe("http://archon.test", client=client)
        assert isinstance(result, HealthProbeResult)
        assert result.ok is True
        assert result.status_code == 200
        assert result.reachable is True
        assert "healthy" in result.summary

    def test_5xx_response_is_unhealthy(self):
        client = _stub_client(response=httpx.Response(503))
        result = archon_health_probe("http://archon.test", client=client)
        assert result.ok is False
        assert result.status_code == 503
        assert result.reachable is True
        assert "503" in result.summary

    def test_connect_error_is_unreachable(self):
        client = _stub_client(raises=httpx.ConnectError("name not resolved"))
        result = archon_health_probe("http://archon.test", client=client)
        assert result.ok is False
        assert result.status_code is None
        assert result.reachable is False
        assert "connect error" in result.summary

    def test_timeout_is_unreachable(self):
        client = _stub_client(raises=httpx.ReadTimeout("timed out"))
        result = archon_health_probe(
            "http://archon.test", client=client, timeout_seconds=2,
        )
        assert result.ok is False
        assert result.status_code is None
        assert result.reachable is False
        assert "timeout" in result.summary.lower()


def _config(tmp_path: Path) -> ArchonWorkflowConfig:
    return ArchonWorkflowConfig(
        run_id="run-h",
        goal_text="hello",
        constraints_text=None,
        repo_path=tmp_path / "repo",
        task_branch="auto/h",
        workflow_type="goal",
        timeout_seconds=10,
    )


class TestHttpArchonAdapter:
    def test_health_probe_unreachable_returns_failure_result(self, tmp_path, monkeypatch):
        """When archon isn't running, the adapter still returns a structured
        ArchonRunResult — it doesn't raise."""
        from operations_center.backends.archon import http_client as hc

        def _fake_probe(base_url, **_kw):
            return HealthProbeResult(
                ok=False, base_url=base_url, status_code=None,
                summary="connect error: name not resolved",
            )

        monkeypatch.setattr(hc, "archon_health_probe", _fake_probe)

        adapter = HttpArchonAdapter(base_url="http://archon.test")
        result = adapter.run(_config(tmp_path))
        assert result.outcome == "failure"
        assert "unreachable" in result.error_text.lower()

    def test_health_probe_ok_still_failure_with_not_implemented_message(
        self, tmp_path, monkeypatch,
    ):
        """Health probe ok → run() still returns failure, with explicit
        'workflow dispatch not implemented' message rather than misleading
        success."""
        from operations_center.backends.archon import http_client as hc

        def _fake_probe(base_url, **_kw):
            return HealthProbeResult(
                ok=True, base_url=base_url, status_code=200,
                summary="archon healthy",
            )

        monkeypatch.setattr(hc, "archon_health_probe", _fake_probe)

        adapter = HttpArchonAdapter(base_url="http://archon.test")
        result = adapter.run(_config(tmp_path))
        assert result.outcome == "failure"
        assert "not yet implemented" in result.error_text

    def test_default_base_url(self, tmp_path):
        """Default base_url points at the WorkStation-deployed archon."""
        adapter = HttpArchonAdapter()
        assert adapter.base_url == "http://localhost:3000"
