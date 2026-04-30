# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for backends/openclaw/invoke.py."""

from __future__ import annotations

import pytest

from operations_center.backends.openclaw.invoke import (
    OpenClawBackendInvoker,
    OpenClawRunResult,
    OpenClawRunner,
    StubOpenClawRunner,
)
from operations_center.backends.openclaw.models import (
    OpenClawPreparedRun,
    OpenClawRunCapture,
)
from pathlib import Path


def _prepared(**kw) -> OpenClawPreparedRun:
    defaults = dict(
        run_id="run-inv-01",
        goal_text="Fix lint errors",
        constraints_text=None,
        repo_path=Path("/workspace/repo"),
        task_branch="auto/fix-lint-abc",
        run_mode="goal",
        timeout_seconds=300,
        validation_commands=[],
        metadata={},
    )
    defaults.update(kw)
    return OpenClawPreparedRun(**defaults)


def _stub(
    outcome: str = "success",
    exit_code: int = 0,
    output_text: str = "openclaw: done",
    error_text: str = "",
    events: list[dict] | None = None,
    reported_changed_files: list[dict] | None = None,
) -> StubOpenClawRunner:
    return StubOpenClawRunner(
        OpenClawRunResult(
            outcome=outcome,
            exit_code=exit_code,
            output_text=output_text,
            error_text=error_text,
            events=events or [],
            reported_changed_files=reported_changed_files or [],
        )
    )


# ---------------------------------------------------------------------------
# StubOpenClawRunner
# ---------------------------------------------------------------------------


def test_stub_returns_configured_result():
    stub = _stub(outcome="success")
    result = stub.run(_prepared())
    assert result.outcome == "success"


def test_stub_injects_output_text():
    stub = _stub(output_text="openclaw: 3 files fixed")
    result = stub.run(_prepared())
    assert "3 files fixed" in result.output_text


def test_stub_injects_events():
    stub = _stub(events=[{"type": "tool_use", "name": "write_file"}])
    result = stub.run(_prepared())
    assert len(result.events) == 1


def test_stub_injects_reported_changed_files():
    stub = _stub(reported_changed_files=[{"path": "src/main.py", "change_type": "modified"}])
    result = stub.run(_prepared())
    assert len(result.reported_changed_files) == 1


def test_base_runner_is_abstract():
    with pytest.raises(TypeError):
        OpenClawRunner()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# OpenClawBackendInvoker
# ---------------------------------------------------------------------------


def test_invoker_returns_capture():
    invoker = OpenClawBackendInvoker(_stub())
    capture = invoker.invoke(_prepared())
    assert isinstance(capture, OpenClawRunCapture)


def test_invoker_preserves_run_id():
    invoker = OpenClawBackendInvoker(_stub())
    capture = invoker.invoke(_prepared(run_id="run-xyz-99"))
    assert capture.run_id == "run-xyz-99"


def test_invoker_preserves_outcome():
    invoker = OpenClawBackendInvoker(_stub(outcome="success"))
    capture = invoker.invoke(_prepared())
    assert capture.outcome == "success"


def test_invoker_failure_outcome():
    invoker = OpenClawBackendInvoker(_stub(outcome="failure", exit_code=1, error_text="tool error"))
    capture = invoker.invoke(_prepared())
    assert capture.outcome == "failure"
    assert not capture.succeeded


def test_invoker_timeout_detection_from_outcome():
    invoker = OpenClawBackendInvoker(_stub(outcome="timeout"))
    capture = invoker.invoke(_prepared())
    assert capture.timeout_hit is True


def test_invoker_timeout_detection_from_error_text():
    invoker = OpenClawBackendInvoker(_stub(outcome="failure", error_text="[timeout: 300s]"))
    capture = invoker.invoke(_prepared())
    assert capture.timeout_hit is True


def test_invoker_deadline_exceeded_detection():
    invoker = OpenClawBackendInvoker(_stub(outcome="failure", error_text="deadline exceeded"))
    capture = invoker.invoke(_prepared())
    assert capture.timeout_hit is True


def test_invoker_no_timeout_on_success():
    invoker = OpenClawBackendInvoker(_stub(outcome="success"))
    capture = invoker.invoke(_prepared())
    assert capture.timeout_hit is False


def test_invoker_duration_ms_set():
    invoker = OpenClawBackendInvoker(_stub())
    capture = invoker.invoke(_prepared())
    assert capture.duration_ms >= 0


def test_invoker_events_preserved():
    events = [{"type": "tool_use", "name": "read_file"}, {"type": "message", "text": "done"}]
    invoker = OpenClawBackendInvoker(_stub(events=events))
    capture = invoker.invoke(_prepared())
    assert capture.event_count == 2


def test_invoker_reported_changed_files_preserved():
    files = [{"path": "src/a.py", "change_type": "modified"}]
    invoker = OpenClawBackendInvoker(_stub(reported_changed_files=files))
    capture = invoker.invoke(_prepared())
    assert len(capture.reported_changed_files) == 1


def test_invoker_changed_files_source_event_stream():
    files = [{"path": "src/a.py", "change_type": "modified"}]
    invoker = OpenClawBackendInvoker(_stub(reported_changed_files=files))
    capture = invoker.invoke(_prepared())
    assert capture.changed_files_source == "event_stream"


def test_invoker_changed_files_source_unknown_when_no_files():
    invoker = OpenClawBackendInvoker(_stub())
    capture = invoker.invoke(_prepared())
    assert capture.changed_files_source == "unknown"


def test_invoker_artifacts_extracted_from_output():
    invoker = OpenClawBackendInvoker(_stub(output_text="openclaw: refactored 3 files"))
    capture = invoker.invoke(_prepared())
    assert len(capture.artifacts) >= 1
    assert any("openclaw" in a.label for a in capture.artifacts)


def test_invoker_succeeds_property():
    invoker = OpenClawBackendInvoker(_stub(outcome="success"))
    capture = invoker.invoke(_prepared())
    assert capture.succeeded is True


def test_invoker_failure_succeeds_property():
    invoker = OpenClawBackendInvoker(_stub(outcome="failure", exit_code=1))
    capture = invoker.invoke(_prepared())
    assert capture.succeeded is False


def test_invoker_requires_no_switchboard_proxy():
    invoker = OpenClawBackendInvoker(_stub())
    capture = invoker.invoke(_prepared())
    assert capture.run_id == "run-inv-01"
