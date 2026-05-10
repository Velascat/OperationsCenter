# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Regression tests for the G-002 and G-003 fixes from the real R6 run."""
from __future__ import annotations


from cxrp.vocabulary.status import ExecutionStatus

from operations_center.contracts.execution import RuntimeBindingSummary
from operations_center.executors.kodo.binder import bind
from operations_center.executors.kodo.normalizer import normalize


# ── G-002: binder derives --orchestrator string ──────────────────────────


class TestG002OrchestratorDerivation:
    def test_opus_binding_yields_claude_code_opus(self):
        sel = bind(RuntimeBindingSummary(
            kind="cli_subscription", selection_mode="explicit_request",
            provider="anthropic", model="opus",
        ))
        assert sel.orchestrator == "claude-code:opus"

    def test_sonnet_binding_yields_claude_code_sonnet(self):
        sel = bind(RuntimeBindingSummary(
            kind="cli_subscription", selection_mode="explicit_request",
            provider="anthropic", model="sonnet",
        ))
        assert sel.orchestrator == "claude-code:sonnet"

    def test_haiku_binding_yields_claude_code_haiku(self):
        sel = bind(RuntimeBindingSummary(
            kind="cli_subscription", selection_mode="explicit_request",
            provider="anthropic", model="haiku",
        ))
        assert sel.orchestrator == "claude-code:haiku"

    def test_unspecified_model_defaults_to_sonnet(self):
        sel = bind(RuntimeBindingSummary(
            kind="cli_subscription", selection_mode="explicit_request",
            provider="anthropic",
        ))
        assert sel.orchestrator == "claude-code:sonnet"

    def test_backend_default_no_orchestrator_override(self):
        sel = bind(None)
        assert sel.orchestrator is None


# ── G-003: normalizer scans stdout for stage failures ────────────────────


class TestG003StdoutFailureDetection:
    def test_exit_zero_with_stage_crash_marked_failed(self):
        """Real R6 case — kodo returned 0 despite stage crash."""
        res = normalize({
            "exit_code": 0,
            "stdout": "Done: 0/1 stage completed; Stage 1 (Append line) crashed: kwarg error",
        })
        assert res.status == ExecutionStatus.FAILED
        assert res.ok is False
        assert "internal stage failure" in res.evidence.failure_reason

    def test_exit_zero_with_done_zero_marker_marked_failed(self):
        res = normalize({
            "exit_code": 0,
            "stdout": "blah blah\nDone: 0/3 stages completed\nblah",
        })
        assert res.status == ExecutionStatus.FAILED

    def test_exit_zero_with_clean_stdout_remains_succeeded(self):
        """Don't false-positive on success runs."""
        res = normalize({
            "exit_code": 0,
            "stdout": "Done: 1/1 stage completed",
            "files_changed": ["README.md"],
        })
        assert res.status == ExecutionStatus.SUCCEEDED
        assert res.ok is True

    def test_stopping_run_marker_caught(self):
        res = normalize({
            "exit_code": 0,
            "stdout": "[orchestrator] Stopping run — stage did not complete",
        })
        assert res.status == ExecutionStatus.FAILED

    def test_failure_reason_falls_back_to_stdout_when_stderr_empty(self):
        res = normalize({
            "exit_code": 0,
            "stdout": "Done: 0/1 stage completed",
            "stderr": "",
        })
        assert res.evidence.failure_reason is not None
        assert "internal stage failure" in res.evidence.failure_reason

    def test_existing_stderr_failure_still_wins_over_stdout(self):
        res = normalize({
            "exit_code": 1,
            "stderr": "explicit stderr error\nfinal line",
            "stdout": "Done: 0/1 stage completed",
        })
        # Real failure with real stderr: still uses stderr's last line
        assert res.evidence.failure_reason == "final line"
