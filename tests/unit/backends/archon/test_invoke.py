"""Tests for backends/archon/invoke.py — ArchonBackendInvoker and StubArchonAdapter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from control_plane.backends.archon.invoke import (
    ArchonAdapter,
    ArchonBackendInvoker,
    ArchonRunResult,
    StubArchonAdapter,
)
from control_plane.backends.archon.models import ArchonWorkflowConfig


def _config(**kw) -> ArchonWorkflowConfig:
    defaults = dict(
        run_id="run-001",
        goal_text="Refactor the login module",
        constraints_text=None,
        repo_path=Path("/tmp/repo"),
        task_branch="auto/refactor-login",
        workflow_type="goal",
        timeout_seconds=300,
        validation_commands=[],
    )
    defaults.update(kw)
    return ArchonWorkflowConfig(**defaults)


def _mock_archon(outcome="success", output_text="done", error_text="", events=None) -> ArchonAdapter:
    adapter = MagicMock(spec=ArchonAdapter)
    adapter.run.return_value = ArchonRunResult(
        outcome=outcome,
        exit_code=0 if outcome == "success" else 1,
        output_text=output_text,
        error_text=error_text,
        workflow_events=events or [],
    )
    return adapter


def _invoker(adapter=None) -> ArchonBackendInvoker:
    if adapter is None:
        adapter = _mock_archon()
    return ArchonBackendInvoker(adapter)


# ---------------------------------------------------------------------------
# StubArchonAdapter
# ---------------------------------------------------------------------------


def test_stub_returns_configured_result():
    result = ArchonRunResult(outcome="success", output_text="stub result")
    stub = StubArchonAdapter(result)
    actual = stub.run(_config())
    assert actual is result


def test_stub_different_configs_same_result():
    result = ArchonRunResult(outcome="failure")
    stub = StubArchonAdapter(result)
    assert stub.run(_config(run_id="a")).outcome == "failure"
    assert stub.run(_config(run_id="b")).outcome == "failure"


# ---------------------------------------------------------------------------
# ArchonAdapter base — raises NotImplementedError
# ---------------------------------------------------------------------------


def test_base_adapter_raises():
    adapter = ArchonAdapter()
    with pytest.raises(NotImplementedError):
        adapter.run(_config())


# ---------------------------------------------------------------------------
# ArchonBackendInvoker — structure
# ---------------------------------------------------------------------------


def test_invoke_returns_run_capture():
    from control_plane.backends.archon.models import ArchonRunCapture
    invoker = _invoker()
    capture = invoker.invoke(_config())
    assert isinstance(capture, ArchonRunCapture)


def test_capture_run_id_matches_config():
    invoker = _invoker()
    config = _config(run_id="test-run-42")
    capture = invoker.invoke(config)
    assert capture.run_id == "test-run-42"


def test_capture_outcome_from_adapter():
    adapter = _mock_archon(outcome="failure")
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert capture.outcome == "failure"


def test_capture_output_text_from_adapter():
    adapter = _mock_archon(output_text="archon: workflow completed")
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert capture.output_text == "archon: workflow completed"


def test_capture_error_text_from_adapter():
    adapter = _mock_archon(error_text="archon: step 2 failed")
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert capture.error_text == "archon: step 2 failed"


def test_capture_workflow_events_from_adapter():
    events = [{"step": "plan", "status": "ok"}, {"step": "execute", "status": "ok"}]
    adapter = _mock_archon(events=events)
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert len(capture.workflow_events) == 2


def test_capture_duration_ms_is_set():
    invoker = _invoker()
    capture = invoker.invoke(_config())
    assert capture.duration_ms >= 0


def test_capture_started_and_finished_at():
    invoker = _invoker()
    capture = invoker.invoke(_config())
    assert isinstance(capture.started_at, datetime)
    assert isinstance(capture.finished_at, datetime)
    assert capture.finished_at >= capture.started_at


# ---------------------------------------------------------------------------
# Timeout detection
# ---------------------------------------------------------------------------


def test_timeout_outcome_sets_timeout_hit():
    adapter = _mock_archon(outcome="timeout", error_text="")
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert capture.timeout_hit is True


def test_timeout_signal_in_error_text_sets_timeout_hit():
    adapter = _mock_archon(outcome="failure", error_text="[timeout: process killed]")
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert capture.timeout_hit is True


def test_non_timeout_outcome_does_not_set_timeout_hit():
    adapter = _mock_archon(outcome="failure", error_text="something else failed")
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert capture.timeout_hit is False


# ---------------------------------------------------------------------------
# Artifact extraction from output
# ---------------------------------------------------------------------------


def test_log_excerpt_artifact_extracted_from_output():
    adapter = _mock_archon(output_text="archon: done", error_text="")
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    labels = [a.label for a in capture.artifacts]
    assert any("archon" in label.lower() for label in labels)


def test_no_artifact_when_output_empty():
    adapter = _mock_archon(output_text="", error_text="")
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert len(capture.artifacts) == 0


def test_artifact_content_truncated_at_4000():
    long_output = "x" * 8000
    adapter = _mock_archon(output_text=long_output)
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert len(capture.artifacts[0].content) < 5000


# ---------------------------------------------------------------------------
# ArchonRunCapture properties
# ---------------------------------------------------------------------------


def test_succeeded_true_when_outcome_success():
    adapter = _mock_archon(outcome="success")
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert capture.succeeded is True


def test_succeeded_false_when_outcome_failure():
    adapter = _mock_archon(outcome="failure")
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert capture.succeeded is False


def test_combined_output_combines_stdout_and_stderr():
    adapter = _mock_archon(output_text="out", error_text="err")
    invoker = _invoker(adapter)
    capture = invoker.invoke(_config())
    assert "out" in capture.combined_output
    assert "err" in capture.combined_output


def test_switchboard_url_is_legacy_compatibility_only(tmp_path):
    adapter = _mock_archon()
    with pytest.warns(DeprecationWarning, match="legacy compatibility-only"):
        invoker = ArchonBackendInvoker(adapter, switchboard_url="http://sb:20401")

    capture = invoker.invoke(_config(repo_path=tmp_path))
    assert capture.run_id == "run-001"
