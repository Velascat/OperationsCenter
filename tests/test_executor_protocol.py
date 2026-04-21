# tests/test_executor_protocol.py
"""Tests for the executor protocol — interface contract and dataclass shapes."""
from __future__ import annotations

from pathlib import Path

import pytest

from control_plane.adapters.executor.protocol import Executor, ExecutorResult, ExecutorTask


class TestExecutorTask:
    def test_required_fields(self, tmp_path: Path) -> None:
        task = ExecutorTask(goal="do something", repo_path=tmp_path)
        assert task.goal == "do something"
        assert task.repo_path == tmp_path

    def test_optional_fields_have_defaults(self, tmp_path: Path) -> None:
        task = ExecutorTask(goal="x", repo_path=tmp_path)
        assert task.constraints == ""
        assert task.metadata == {}

    def test_metadata_is_mutable(self, tmp_path: Path) -> None:
        task = ExecutorTask(goal="x", repo_path=tmp_path)
        task.metadata["kodo_mode"] = "improve"
        assert task.metadata["kodo_mode"] == "improve"


class TestExecutorResult:
    def test_required_fields(self) -> None:
        r = ExecutorResult(success=True, output="done")
        assert r.success is True
        assert r.output == "done"

    def test_optional_fields_have_defaults(self) -> None:
        r = ExecutorResult(success=False, output="")
        assert r.exit_code is None
        assert r.executor == ""
        assert r.metadata == {}

    def test_success_false_with_exit_code(self) -> None:
        r = ExecutorResult(success=False, output="err", exit_code=1, executor="kodo")
        assert r.success is False
        assert r.exit_code == 1


class TestExecutorProtocol:
    def test_adapter_satisfies_protocol(self, tmp_path: Path) -> None:
        """Any class with execute() and name() satisfies the Executor protocol."""

        class FakeExecutor:
            def execute(self, task: ExecutorTask) -> ExecutorResult:
                return ExecutorResult(success=True, output="ok", executor=self.name())

            def name(self) -> str:
                return "fake"

        fake = FakeExecutor()
        assert isinstance(fake, Executor)

    def test_class_missing_execute_does_not_satisfy_protocol(self) -> None:
        class Incomplete:
            def name(self) -> str:
                return "incomplete"

        assert not isinstance(Incomplete(), Executor)

    def test_class_missing_name_does_not_satisfy_protocol(self) -> None:
        class Incomplete:
            def execute(self, task: ExecutorTask) -> ExecutorResult:
                return ExecutorResult(success=True, output="")

        assert not isinstance(Incomplete(), Executor)
