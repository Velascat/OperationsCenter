# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""R2 — KodoBackendAdapter writes .kodo/team.json from RuntimeBinding."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from operations_center.backends.kodo.adapter import KodoBackendAdapter
from operations_center.backends.kodo.models import KodoRunCapture
from operations_center.contracts.execution import (
    ExecutionRequest, ExecutionResult, RuntimeBindingSummary,
)
from operations_center.contracts.enums import ExecutionStatus


def _request(workspace: Path, *, binding: RuntimeBindingSummary | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        proposal_id="p", decision_id="d",
        goal_text="design auth subsystem",
        repo_key="r", clone_url="https://x", base_branch="main",
        task_branch="feat/auth",
        workspace_path=workspace,
        runtime_binding=binding,
    )


def _build_adapter(*, capture_to_return: KodoRunCapture) -> KodoBackendAdapter:
    """Build a KodoBackendAdapter whose invoker returns a stubbed capture."""
    kodo_raw = MagicMock()
    adapter = KodoBackendAdapter.__new__(KodoBackendAdapter)
    adapter._invoker = MagicMock()
    adapter._invoker.invoke.return_value = capture_to_return
    adapter._kodo_mode = "goal"
    return adapter


def _stub_capture() -> KodoRunCapture:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return KodoRunCapture(
        run_id="rc-1",
        exit_code=0,
        stdout="ok",
        stderr="",
        command=["kodo", "--goal-file", "g.md"],
        started_at=now,
        finished_at=now,
        duration_ms=1234,
    )


def test_binder_writes_team_json_to_workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    binding = RuntimeBindingSummary(
        kind="cli_subscription", selection_mode="explicit_request",
        provider="anthropic", model="opus",
    )
    request = _request(workspace, binding=binding)
    adapter = _build_adapter(capture_to_return=_stub_capture())

    # Capture the team file's contents during invocation by stashing them
    team_seen: dict = {}
    def _capture_team(*args, **kwargs):
        team_path = workspace / ".kodo" / "team.json"
        if team_path.exists():
            team_seen.update(json.loads(team_path.read_text()))
        return _stub_capture()
    adapter._invoker.invoke.side_effect = _capture_team

    # Bypass support check by stubbing it
    from unittest.mock import patch
    with patch("operations_center.backends.kodo.adapter.check_support") as cs, \
         patch("operations_center.backends.kodo.adapter.map_request") as mr, \
         patch("operations_center.backends.kodo.adapter.normalize") as norm:
        cs.return_value = MagicMock(supported=True)
        mr.return_value = MagicMock()
        norm.return_value = ExecutionResult(
            run_id="r1", proposal_id="p", decision_id="d",
            status=ExecutionStatus.SUCCEEDED,
            success=True,
        )
        result, capture = adapter.execute_and_capture(request)

    # 1. Team file existed during invocation, with the right team
    assert team_seen.get("agents", {}).get("worker_smart", {}).get("model") == "opus"
    # 2. Team file cleaned up after invocation
    assert not (workspace / ".kodo" / "team.json").exists()
    # 3. observed_runtime attached to capture for drift detection
    assert getattr(capture, "observed_runtime", None) is not None
    assert capture.observed_runtime["model"] == "opus"
    assert getattr(capture, "binder_label") == "claude_fallback_team"


def test_no_binder_when_request_lacks_binding(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    request = _request(workspace, binding=None)
    adapter = _build_adapter(capture_to_return=_stub_capture())

    from unittest.mock import patch
    with patch("operations_center.backends.kodo.adapter.check_support") as cs, \
         patch("operations_center.backends.kodo.adapter.map_request") as mr, \
         patch("operations_center.backends.kodo.adapter.normalize") as norm:
        cs.return_value = MagicMock(supported=True)
        mr.return_value = MagicMock()
        norm.return_value = ExecutionResult(
            run_id="r1", proposal_id="p", decision_id="d",
            status=ExecutionStatus.SUCCEEDED, success=True,
        )
        _, capture = adapter.execute_and_capture(request)

    assert getattr(capture, "observed_runtime", None) is None
    assert not (workspace / ".kodo" / "team.json").exists()


def test_bind_error_returns_invocation_error_result(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Unsupported runtime kind for kodo (hosted_api) → BindError
    binding = RuntimeBindingSummary(
        kind="hosted_api", selection_mode="explicit_request",
        provider="anthropic", model="opus", endpoint="https://x",
    )
    request = _request(workspace, binding=binding)
    adapter = _build_adapter(capture_to_return=_stub_capture())

    from unittest.mock import patch
    with patch("operations_center.backends.kodo.adapter.check_support") as cs, \
         patch("operations_center.backends.kodo.adapter.map_request") as mr:
        cs.return_value = MagicMock(supported=True)
        mr.return_value = MagicMock()
        result, capture = adapter.execute_and_capture(request)

    assert capture is None
    # The result should indicate failure, not silently succeed
    assert result.success is False
