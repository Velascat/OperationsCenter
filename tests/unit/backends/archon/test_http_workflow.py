# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""End-to-end tests for ArchonHttpWorkflowDispatcher.

The transport layer is replaced with httpx.MockTransport scripts that
mirror Archon's real conversation/dispatch/poll/abandon sequence.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from executor_runtime import ExecutorRuntime
from executor_runtime.runners import AsyncHttpRunner

from operations_center.backends.archon.http_workflow import (
    ArchonHttpWorkflowDispatcher,
)
from operations_center.backends.archon.models import (
    ArchonRunCapture,
    ArchonWorkflowConfig,
)


# Fixed Archon URL paths for matching in mock transport.
HEALTH = "/api/health"
CONVS = "/api/conversations"
WORKFLOW_RUN_PREFIX = "/api/workflows/"
RUN_PREFIX = "/api/workflows/runs/"
BY_WORKER = "/api/workflows/runs/by-worker/"


def _config(tmp_path: Path, *, workflow_type: str = "goal") -> ArchonWorkflowConfig:
    return ArchonWorkflowConfig(
        run_id="run-1",
        goal_text="please refactor the login flow",
        constraints_text=None,
        repo_path=tmp_path,
        task_branch="auto/login",
        workflow_type=workflow_type,
        timeout_seconds=30,
    )


class _Recorder:
    """Records the URL+method of every request the mock receives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def record(self, request: httpx.Request) -> None:
        self.calls.append((request.method, str(request.url.path)))


def _mock_archon_transport(
    *,
    healthy: bool = True,
    conversation_id: str = "cv-abc",
    run_id: str = "run-xyz",
    final_status: str = "completed",
    final_metadata: dict | None = None,
    by_worker_404_count: int = 0,
    final_events: list[dict] | None = None,
    recorder: _Recorder | None = None,
) -> httpx.MockTransport:
    """Build a transport that walks a successful Archon dispatch flow.

    Parameters control the terminal status and any pre-registration 404s.
    """
    final_metadata = final_metadata or {}
    final_events = final_events or [
        {"event_type": "node_started", "data": {}},
        {"event_type": "node_completed", "data": {"node_output": "all done"}},
    ]
    state = {"by_worker_remaining_404s": by_worker_404_count, "polls": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.record(request)
        path = request.url.path
        method = request.method

        if path == HEALTH and method == "GET":
            if not healthy:
                return httpx.Response(503, text="down")
            return httpx.Response(200, json={"status": "ok"})

        if path == CONVS and method == "POST":
            return httpx.Response(200, json={
                "conversationId": conversation_id, "id": "db-1",
            })

        if path.startswith(WORKFLOW_RUN_PREFIX) and path.endswith("/run") \
                and method == "POST":
            # Archon's real dispatch: 200 + {accepted, status:"started"}
            return httpx.Response(200, json={
                "accepted": True, "status": "started",
            })

        if path.startswith(BY_WORKER) and method == "GET":
            # AsyncHttpRunner polls this URL during the run.
            state["polls"] += 1
            if state["by_worker_remaining_404s"] > 0:
                state["by_worker_remaining_404s"] -= 1
                return httpx.Response(404, text="not registered yet")
            # First successful poll returns "running"; second returns terminal.
            if state["polls"] - by_worker_404_count == 1:
                return httpx.Response(200, json={
                    "run": {"id": run_id, "status": "running",
                            "metadata": final_metadata},
                })
            return httpx.Response(200, json={
                "run": {"id": run_id, "status": final_status,
                        "metadata": final_metadata},
            })

        if path == f"{RUN_PREFIX}{run_id}" and method == "GET":
            # Final run-detail GET — full {run, events}.
            return httpx.Response(200, json={
                "run": {"id": run_id, "status": final_status,
                        "metadata": final_metadata},
                "events": final_events,
            })

        if path == f"{RUN_PREFIX}{run_id}/abandon" and method == "POST":
            return httpx.Response(200, json={"success": True})

        if path == f"{RUN_PREFIX}{run_id}/cancel" and method == "POST":
            return httpx.Response(200, json={"success": True})

        return httpx.Response(404, text=f"unmatched: {method} {path}")

    return httpx.MockTransport(_handler)


def _make_dispatcher(transport: httpx.MockTransport) -> ArchonHttpWorkflowDispatcher:
    """Build a dispatcher whose http calls and AsyncHttpRunner share a transport."""
    runtime = ExecutorRuntime()
    runtime.register("http_async", AsyncHttpRunner(
        client=httpx.Client(transport=transport),
        sleep=lambda *_: None,
    ))
    dispatcher = ArchonHttpWorkflowDispatcher(
        base_url="http://archon.test",
        runtime=runtime,
        poll_interval_seconds=0.0,
    )
    return dispatcher


@pytest.fixture(autouse=True)
def _patch_http_client(monkeypatch):
    """Route http_client helpers through a per-test mock transport.

    Each test sets ``_TRANSPORT`` on this fixture; the helpers create a
    new ``httpx.Client`` bound to it.
    """
    from operations_center.backends.archon import http_client

    holder: dict = {"transport": None}

    def _ctor(*_args, **kwargs):
        if holder["transport"] is None:
            return httpx.Client(**kwargs)
        # Strip transport-incompatible kwargs (timeout is fine).
        timeout = kwargs.get("timeout", None)
        return httpx.Client(transport=holder["transport"], timeout=timeout)

    # Patch httpx.Client construction inside the http_client module.
    monkeypatch.setattr(http_client, "httpx", _make_httpx_proxy(_ctor))
    # http_workflow imports archon_* helpers directly; they internally use
    # httpx.Client which is now the proxy, so no change needed there.

    yield holder


def _make_httpx_proxy(ctor):
    """Wrap the real httpx module but replace Client with a custom ctor."""
    import httpx as real_httpx

    class _Proxy:
        Response = real_httpx.Response
        ConnectError = real_httpx.ConnectError
        TimeoutException = real_httpx.TimeoutException
        ReadTimeout = real_httpx.ReadTimeout

        def __init__(self) -> None: ...

        @staticmethod
        def Client(*args, **kwargs):
            return ctor(*args, **kwargs)

    return _Proxy()


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_completed_workflow_full_flow(self, tmp_path, _patch_http_client):
        recorder = _Recorder()
        transport = _mock_archon_transport(recorder=recorder)
        _patch_http_client["transport"] = transport
        dispatcher = _make_dispatcher(transport)

        capture = dispatcher.dispatch(_config(tmp_path))

        assert isinstance(capture, ArchonRunCapture)
        assert capture.outcome == "success"
        assert capture.exit_code == 0
        assert capture.timeout_hit is False
        assert capture.output_text == "all done"
        assert len(capture.workflow_events) >= 1

        # Sanity: the flow visited each expected step.
        paths = [p for _, p in recorder.calls]
        assert "/api/health" in paths
        assert "/api/conversations" in paths
        assert "/api/workflows/archon-goal-default/run" in paths
        assert "/api/workflows/runs/by-worker/cv-abc" in paths
        assert "/api/workflows/runs/run-xyz" in paths
        assert "/api/workflows/runs/run-xyz/abandon" in paths

    def test_pre_registration_404s_tolerated(self, tmp_path, _patch_http_client):
        transport = _mock_archon_transport(by_worker_404_count=2)
        _patch_http_client["transport"] = transport
        dispatcher = _make_dispatcher(transport)

        capture = dispatcher.dispatch(_config(tmp_path))
        assert capture.outcome == "success"

    def test_workflow_name_mapping_used(self, tmp_path, _patch_http_client):
        recorder = _Recorder()
        transport = _mock_archon_transport(recorder=recorder)
        _patch_http_client["transport"] = transport
        dispatcher = _make_dispatcher(transport)

        capture = dispatcher.dispatch(_config(tmp_path, workflow_type="fix_pr"))
        assert capture.outcome == "success"
        paths = [p for _, p in recorder.calls]
        assert any(
            p == "/api/workflows/archon-fix-github-issue-dag/run" for p in paths
        )


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestFailurePaths:
    def test_unhealthy_short_circuits(self, tmp_path, _patch_http_client):
        transport = _mock_archon_transport(healthy=False)
        _patch_http_client["transport"] = transport
        dispatcher = _make_dispatcher(transport)
        capture = dispatcher.dispatch(_config(tmp_path))
        assert capture.outcome == "failure"
        assert "unreachable" in capture.error_text

    def test_unknown_workflow_type(self, tmp_path, _patch_http_client):
        transport = _mock_archon_transport()
        _patch_http_client["transport"] = transport
        dispatcher = _make_dispatcher(transport)
        capture = dispatcher.dispatch(
            _config(tmp_path, workflow_type="not-a-real-type"),
        )
        assert capture.outcome == "failure"
        assert "unknown archon workflow_type" in capture.error_text

    def test_workflow_failure_status(self, tmp_path, _patch_http_client):
        transport = _mock_archon_transport(
            final_status="failed",
            final_metadata={"failure_reason": "node 2 crashed"},
        )
        _patch_http_client["transport"] = transport
        dispatcher = _make_dispatcher(transport)
        capture = dispatcher.dispatch(_config(tmp_path))
        assert capture.outcome == "failure"
        assert capture.exit_code == 1
        assert "node 2 crashed" in capture.error_text

    def test_workflow_cancelled_status(self, tmp_path, _patch_http_client):
        transport = _mock_archon_transport(final_status="cancelled")
        _patch_http_client["transport"] = transport
        dispatcher = _make_dispatcher(transport)
        capture = dispatcher.dispatch(_config(tmp_path))
        assert capture.outcome == "failure"
        assert "cancelled" in capture.error_text


# ---------------------------------------------------------------------------
# Approval-gate / partial outcome
# ---------------------------------------------------------------------------


class TestPausedApproval:
    def test_paused_maps_to_partial_and_is_not_abandoned(
        self, tmp_path, _patch_http_client,
    ):
        recorder = _Recorder()
        transport = _mock_archon_transport(
            final_status="paused",
            final_metadata={"approval": {"nodeId": "human-review"}},
            recorder=recorder,
        )
        _patch_http_client["transport"] = transport
        dispatcher = _make_dispatcher(transport)
        capture = dispatcher.dispatch(_config(tmp_path))

        assert capture.outcome == "partial"
        assert capture.exit_code == 2
        assert "human-review" in capture.error_text

        # D2: paused runs are NOT abandoned (operator may /approve later).
        paths = [p for _, p in recorder.calls]
        assert not any(p.endswith("/abandon") for p in paths)


# ---------------------------------------------------------------------------
# Output extraction
# ---------------------------------------------------------------------------


class TestOutputExtraction:
    def test_output_text_from_last_node_completed(
        self, tmp_path, _patch_http_client,
    ):
        events = [
            {"event_type": "node_started", "data": {}},
            {"event_type": "node_completed", "data": {"node_output": "first"}},
            {"event_type": "node_started", "data": {}},
            {"event_type": "node_completed", "data": {"node_output": "second"}},
        ]
        transport = _mock_archon_transport(final_events=events)
        _patch_http_client["transport"] = transport
        dispatcher = _make_dispatcher(transport)
        capture = dispatcher.dispatch(_config(tmp_path))
        assert capture.output_text == "second"

    def test_artifacts_extracted_from_event_data(
        self, tmp_path, _patch_http_client,
    ):
        events = [
            {
                "event_type": "node_completed",
                "data": {
                    "node_output": "ok",
                    "artifacts": [
                        {"label": "log", "content": "line\nline", "type": "log"},
                        {"name": "summary", "body": "x", "artifact_type": "summary"},
                    ],
                },
            },
        ]
        transport = _mock_archon_transport(final_events=events)
        _patch_http_client["transport"] = transport
        dispatcher = _make_dispatcher(transport)
        capture = dispatcher.dispatch(_config(tmp_path))
        assert len(capture.artifacts) == 2
        labels = {a.label for a in capture.artifacts}
        assert "log" in labels
        assert "summary" in labels


# ---------------------------------------------------------------------------
# Strict goal_text — D1
# ---------------------------------------------------------------------------


class TestStrictGoalText:
    def test_goal_text_reaches_archon_verbatim(
        self, tmp_path, _patch_http_client,
    ):
        captured: dict = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            method = request.method
            if path == HEALTH:
                return httpx.Response(200, json={"status": "ok"})
            if path == CONVS:
                return httpx.Response(200, json={
                    "conversationId": "cv", "id": "db",
                })
            if path.endswith("/run") and method == "POST":
                captured["body"] = json.loads(request.read())
                return httpx.Response(200, json={
                    "accepted": True, "status": "started",
                })
            if path.startswith(BY_WORKER):
                return httpx.Response(200, json={
                    "run": {"id": "r1", "status": "completed", "metadata": {}},
                })
            if "/runs/r1" in path and method == "GET":
                return httpx.Response(200, json={
                    "run": {"id": "r1", "status": "completed", "metadata": {}},
                    "events": [],
                })
            if path.endswith("/abandon"):
                return httpx.Response(200)
            return httpx.Response(404, text=f"unmatched: {method} {path}")

        transport = httpx.MockTransport(_handler)
        _patch_http_client["transport"] = transport
        dispatcher = _make_dispatcher(transport)

        cfg = _config(tmp_path)
        # Note: task_branch must NOT be framed into the message body (D1).
        capture = dispatcher.dispatch(cfg)
        assert capture.outcome == "success"
        assert captured["body"]["message"] == "please refactor the login flow"
        assert captured["body"]["conversationId"] == "cv"
        # task_branch lives in metadata only, not in the prompt body.
        body_str = json.dumps(captured["body"])
        assert "auto/login" not in body_str


# ---------------------------------------------------------------------------
# Runtime registration
# ---------------------------------------------------------------------------


class TestRuntimeRegistration:
    def test_dispatcher_registers_async_http_runner_idempotently(self):
        runtime = ExecutorRuntime()
        assert runtime.is_registered("http_async") is False
        ArchonHttpWorkflowDispatcher(runtime=runtime)
        assert runtime.is_registered("http_async") is True
        # Constructing a second dispatcher should not error.
        ArchonHttpWorkflowDispatcher(runtime=runtime)
        assert runtime.is_registered("http_async") is True

    def test_pre_registered_runner_is_kept(self):
        runtime = ExecutorRuntime()
        sentinel = AsyncHttpRunner()
        runtime.register("http_async", sentinel)
        ArchonHttpWorkflowDispatcher(runtime=runtime)
        assert runtime._runners["http_async"] is sentinel  # noqa: SLF001
