# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 2 + 3 — RxP wire + ExecutorRuntime delegation for openclaw.

OpenClaw is dispatched to an external runner (the abstract
``OpenClawRunner`` subclass) — same shape as archon's manual-kind
path. These tests pin the RuntimeInvocation/RuntimeResult contract
and verify the invoker routes through ExecutorRuntime.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from executor_runtime import ExecutorRuntime
from executor_runtime.runners import ManualRunner
from rxp.contracts import RuntimeInvocation

from operations_center.backends.openclaw.invoke import (
    OpenClawBackendInvoker,
    OpenClawRunResult,
    StubOpenClawRunner,
    _build_invocation,
    _build_runtime_result,
)
from operations_center.backends.openclaw.models import OpenClawPreparedRun


def _prepared(tmp_path: Path, **kw) -> OpenClawPreparedRun:
    defaults = dict(
        run_id="run-claw-1",
        goal_text="Migrate API",
        constraints_text=None,
        repo_path=tmp_path / "repo",
        task_branch="auto/claw",
        run_mode="goal",
        timeout_seconds=300,
        validation_commands=[],
    )
    defaults.update(kw)
    return OpenClawPreparedRun(**defaults)


class TestBuildInvocation:
    def test_invocation_is_rxp_runtime_invocation(self, tmp_path):
        inv = _build_invocation(_prepared(tmp_path))
        assert isinstance(inv, RuntimeInvocation)

    def test_runtime_kind_is_manual(self, tmp_path):
        inv = _build_invocation(_prepared(tmp_path))
        assert inv.runtime_kind == "manual"

    def test_carries_runtime_metadata(self, tmp_path):
        prepared = _prepared(tmp_path)
        inv = _build_invocation(prepared)
        assert inv.invocation_id == "run-claw-1"
        assert inv.runtime_name == "openclaw"
        assert inv.working_directory == str(prepared.repo_path)
        assert inv.timeout_seconds == 300

    def test_command_captures_run_mode(self, tmp_path):
        inv = _build_invocation(_prepared(tmp_path, run_mode="fix_pr"))
        assert inv.command[0] == "openclaw-run"
        assert "--run-mode" in inv.command
        assert "fix_pr" in inv.command
        assert "--run-id" in inv.command
        assert "run-claw-1" in inv.command

    def test_metadata_carries_run_mode_and_branch(self, tmp_path):
        inv = _build_invocation(_prepared(tmp_path, run_mode="improve", task_branch="my-branch"))
        assert inv.metadata["run_mode"] == "improve"
        assert inv.metadata["task_branch"] == "my-branch"


class TestBuildRuntimeResult:
    def _now(self) -> datetime:
        return datetime.now(tz=timezone.utc)

    def test_success_outcome(self, tmp_path):
        inv = _build_invocation(_prepared(tmp_path))
        raw = OpenClawRunResult(outcome="success", exit_code=0)
        r = _build_runtime_result(invocation=inv, raw=raw, timeout_hit=False,
                                  started_at=self._now(), finished_at=self._now())
        assert r.status == "succeeded"
        assert r.runtime_kind == "manual"

    def test_failure_outcome(self, tmp_path):
        inv = _build_invocation(_prepared(tmp_path))
        raw = OpenClawRunResult(outcome="failure", exit_code=1, error_text="boom")
        r = _build_runtime_result(invocation=inv, raw=raw, timeout_hit=False,
                                  started_at=self._now(), finished_at=self._now())
        assert r.status == "failed"
        assert r.error_summary == "boom"

    def test_timeout_overrides_outcome(self, tmp_path):
        inv = _build_invocation(_prepared(tmp_path))
        raw = OpenClawRunResult(outcome="failure", exit_code=124)
        r = _build_runtime_result(invocation=inv, raw=raw, timeout_hit=True,
                                  started_at=self._now(), finished_at=self._now())
        assert r.status == "timed_out"

    def test_partial_maps_to_succeeded(self, tmp_path):
        inv = _build_invocation(_prepared(tmp_path))
        raw = OpenClawRunResult(outcome="partial", exit_code=0)
        r = _build_runtime_result(invocation=inv, raw=raw, timeout_hit=False,
                                  started_at=self._now(), finished_at=self._now())
        assert r.status == "succeeded"


class TestExecutorRuntimeDelegation:
    def test_invoker_routes_through_executor_runtime(self, tmp_path):
        runner = StubOpenClawRunner(
            OpenClawRunResult(outcome="success", exit_code=0, output_text="ok"),
        )
        runtime = ExecutorRuntime()
        invoker = OpenClawBackendInvoker(runner, runtime=runtime)
        capture = invoker.invoke(_prepared(tmp_path))
        assert capture.outcome == "success"
        assert "manual" in runtime._runners

    def test_default_runtime_works(self, tmp_path):
        runner = StubOpenClawRunner(OpenClawRunResult(outcome="success"))
        invoker = OpenClawBackendInvoker(runner)
        capture = invoker.invoke(_prepared(tmp_path))
        assert capture.outcome == "success"

    def test_dispatcher_receives_runtime_invocation(self, tmp_path):
        captured: list[RuntimeInvocation] = []
        runner = StubOpenClawRunner(OpenClawRunResult(outcome="success"))
        runtime = ExecutorRuntime()
        original_register = runtime.register

        def _spy_register(kind, runner_obj):
            inner = runner_obj._dispatcher

            def _wrapped(invocation):
                captured.append(invocation)
                return inner(invocation)

            original_register(kind, ManualRunner(_wrapped))

        runtime.register = _spy_register

        invoker = OpenClawBackendInvoker(runner, runtime=runtime)
        invoker.invoke(_prepared(tmp_path, run_id="run-spy"))

        assert len(captured) == 1
        assert captured[0].invocation_id == "run-spy"
        assert captured[0].runtime_name == "openclaw"
        assert captured[0].runtime_kind == "manual"

    def test_timeout_propagates_to_capture(self, tmp_path):
        runner = StubOpenClawRunner(
            OpenClawRunResult(outcome="timeout", exit_code=124),
        )
        invoker = OpenClawBackendInvoker(runner)
        capture = invoker.invoke(_prepared(tmp_path))
        assert capture.timeout_hit is True
