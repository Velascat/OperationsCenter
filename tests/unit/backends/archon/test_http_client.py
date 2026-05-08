# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the archon HTTP client (helpers + concrete adapter shim)."""
from __future__ import annotations

from pathlib import Path

import httpx

from operations_center.backends.archon.http_client import (
    ConversationCreateResult,
    HealthProbeResult,
    WorkflowRunDetail,
    WorkflowSummary,
    archon_abandon_run,
    archon_cancel_run,
    archon_create_conversation,
    archon_get_run_by_worker,
    archon_get_run_detail,
    archon_health_probe,
    archon_list_workflows,
)
from operations_center.backends.archon.invoke import (
    ArchonWorkflowConfig,
    HttpArchonAdapter,
)


def _stub_client(*, response: httpx.Response | None = None,
                 raises: Exception | None = None) -> httpx.Client:
    def _handler(request: httpx.Request) -> httpx.Response:
        if raises is not None:
            raise raises
        return response or httpx.Response(200, json={"status": "ok"})

    return httpx.Client(transport=httpx.MockTransport(_handler))


def _scripted_client(steps: list) -> httpx.Client:
    """Replay an ordered list of ``httpx.Response`` objects (or Exceptions)."""
    iterator = iter(steps)

    def _handler(request: httpx.Request) -> httpx.Response:
        nxt = next(iterator)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

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


class TestArchonCreateConversation:
    def test_success(self):
        client = _stub_client(response=httpx.Response(
            200, json={"conversationId": "cv-1", "id": "db-1", "dispatched": True},
        ))
        result = archon_create_conversation("http://archon.test", client=client)
        assert isinstance(result, ConversationCreateResult)
        assert result.ok is True
        assert result.conversation_id == "cv-1"
        assert result.db_id == "db-1"
        assert result.dispatched is True

    def test_with_codebase_id_sends_field(self):
        captured: dict = {}

        def _handler(req: httpx.Request) -> httpx.Response:
            captured["body"] = req.read()
            return httpx.Response(200, json={"conversationId": "cv", "id": "db"})

        client = httpx.Client(transport=httpx.MockTransport(_handler))
        archon_create_conversation(
            "http://archon.test", codebase_id="cb-1", client=client,
        )
        assert b"codebaseId" in captured["body"]
        assert b"cb-1" in captured["body"]

    def test_500_returns_error(self):
        client = _stub_client(response=httpx.Response(500, text="boom"))
        result = archon_create_conversation("http://archon.test", client=client)
        assert result.ok is False
        assert "500" in (result.error_summary or "")

    def test_non_json_returns_error(self):
        client = _stub_client(response=httpx.Response(200, text="not-json"))
        result = archon_create_conversation("http://archon.test", client=client)
        assert result.ok is False
        assert "non-JSON" in (result.error_summary or "")

    def test_connect_error_returns_error(self):
        client = _stub_client(raises=httpx.ConnectError("dns"))
        result = archon_create_conversation("http://archon.test", client=client)
        assert result.ok is False
        assert "http error" in (result.error_summary or "")


class TestArchonGetRunByWorker:
    def test_200_returns_run(self):
        payload = {
            "run": {
                "id": "run-1",
                "status": "running",
                "metadata": {"foo": "bar"},
            },
        }
        client = _stub_client(response=httpx.Response(200, json=payload))
        result = archon_get_run_by_worker("http://archon.test", "cv-1", client=client)
        assert isinstance(result, WorkflowRunDetail)
        assert result.ok is True
        assert result.run_id == "run-1"
        assert result.status == "running"
        # by-worker response has no events; helper returns empty list.
        assert result.events == []

    def test_404_signals_not_registered(self):
        client = _stub_client(response=httpx.Response(404, text="nope"))
        result = archon_get_run_by_worker("http://archon.test", "cv-1", client=client)
        assert result.ok is False
        assert "not registered yet" in (result.error_summary or "")

    def test_500_propagates_error(self):
        client = _stub_client(response=httpx.Response(500, text="server boom"))
        result = archon_get_run_by_worker("http://archon.test", "cv-1", client=client)
        assert result.ok is False
        assert "500" in (result.error_summary or "")


class TestArchonGetRunDetail:
    def test_200_returns_run_with_events(self):
        payload = {
            "run": {"id": "run-1", "status": "completed", "metadata": {}},
            "events": [
                {"event_type": "node_started", "data": {}},
                {"event_type": "node_completed", "data": {"node_output": "hi"}},
            ],
        }
        client = _stub_client(response=httpx.Response(200, json=payload))
        result = archon_get_run_detail("http://archon.test", "run-1", client=client)
        assert result.ok is True
        assert result.run_id == "run-1"
        assert result.status == "completed"
        assert len(result.events) == 2

    def test_empty_run_id_returns_error(self):
        client = _stub_client()
        result = archon_get_run_detail("http://archon.test", "", client=client)
        assert result.ok is False
        assert "run_id" in (result.error_summary or "")

    def test_non_200_returns_error(self):
        client = _stub_client(response=httpx.Response(404, text="missing"))
        result = archon_get_run_detail("http://archon.test", "run-1", client=client)
        assert result.ok is False
        assert "404" in (result.error_summary or "")


class TestArchonAbandon:
    def test_success(self):
        client = _stub_client(response=httpx.Response(200, json={"success": True}))
        ok = archon_abandon_run("http://archon.test", "run-1", client=client)
        assert ok is True

    def test_empty_run_id_no_op(self):
        client = _stub_client()
        ok = archon_abandon_run("http://archon.test", "", client=client)
        assert ok is False

    def test_500_returns_false(self):
        client = _stub_client(response=httpx.Response(500))
        ok = archon_abandon_run("http://archon.test", "run-1", client=client)
        assert ok is False

    def test_connect_error_returns_false(self):
        client = _stub_client(raises=httpx.ConnectError("dns"))
        ok = archon_abandon_run("http://archon.test", "run-1", client=client)
        assert ok is False


class TestArchonCancel:
    def test_success(self):
        client = _stub_client(response=httpx.Response(200))
        ok = archon_cancel_run("http://archon.test", "run-1", client=client)
        assert ok is True

    def test_empty_run_id_no_op(self):
        client = _stub_client()
        ok = archon_cancel_run("http://archon.test", "", client=client)
        assert ok is False

    def test_400_returns_false(self):
        client = _stub_client(response=httpx.Response(400))
        ok = archon_cancel_run("http://archon.test", "run-1", client=client)
        assert ok is False


class TestArchonListWorkflows:
    def test_list_response(self):
        payload = [
            {"name": "archon-assist", "description": "goal"},
            {"name": "archon-fix-github-issue", "description": "fix"},
        ]
        client = _stub_client(response=httpx.Response(200, json=payload))
        result = archon_list_workflows("http://archon.test", client=client)
        assert len(result) == 2
        assert isinstance(result[0], WorkflowSummary)
        assert result[0].name == "archon-assist"

    def test_object_with_workflows_key(self):
        payload = {"workflows": [{"name": "a"}, {"name": "b"}]}
        client = _stub_client(response=httpx.Response(200, json=payload))
        result = archon_list_workflows("http://archon.test", client=client)
        assert [w.name for w in result] == ["a", "b"]

    def test_500_returns_empty(self):
        client = _stub_client(response=httpx.Response(500))
        assert archon_list_workflows("http://archon.test", client=client) == []

    def test_connect_error_returns_empty(self):
        client = _stub_client(raises=httpx.ConnectError("dns"))
        assert archon_list_workflows("http://archon.test", client=client) == []


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
    def test_default_base_url(self):
        adapter = HttpArchonAdapter()
        assert adapter.base_url == "http://localhost:3000"

    def test_health_probe_unreachable_short_circuits_dispatch(
        self, tmp_path, monkeypatch,
    ):
        """When archon isn't running, the adapter returns a structured
        failure ArchonRunResult — it doesn't raise, and doesn't issue further
        HTTP calls past the health probe."""
        from operations_center.backends.archon import http_workflow

        def _fake_probe(base_url, **_kw):
            return HealthProbeResult(
                ok=False, base_url=base_url, status_code=None,
                summary="connect error: name not resolved",
            )

        monkeypatch.setattr(http_workflow, "archon_health_probe", _fake_probe)

        adapter = HttpArchonAdapter(base_url="http://archon.test")
        result = adapter.run(_config(tmp_path))
        assert result.outcome == "failure"
        assert "unreachable" in result.error_text.lower()

    def test_unknown_workflow_type_short_circuits(self, tmp_path, monkeypatch):
        from operations_center.backends.archon import http_workflow

        monkeypatch.setattr(
            http_workflow, "archon_health_probe",
            lambda base_url, **_kw: HealthProbeResult(
                ok=True, base_url=base_url, status_code=200, summary="healthy",
            ),
        )
        adapter = HttpArchonAdapter(base_url="http://archon.test")
        cfg = _config(tmp_path)
        cfg = ArchonWorkflowConfig(
            **{**cfg.__dict__, "workflow_type": "no-such-type"},
        )
        result = adapter.run(cfg)
        assert result.outcome == "failure"
        assert "unknown archon workflow_type" in result.error_text
