# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""G-V01 traceability tests — OC ExecutionResult ↔ RxP RuntimeResult linkage.

Adapters that delegate to ExecutorRuntime must populate
``ExecutionResult.runtime_invocation_ref`` so an audit consumer can reach
the underlying RxP RuntimeResult artifacts (stdout/stderr/artifact_dir)
from the OC result alone. Adapters that do not invoke a runtime (e.g.
demo_stub) must leave the ref None.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rxp.contracts import RuntimeInvocation, RuntimeResult

from operations_center.backends._runtime_ref import runtime_invocation_ref
from operations_center.backends.demo_stub.adapter import DemoStubBackendAdapter
from operations_center.backends.direct_local.adapter import DirectLocalBackendAdapter
from operations_center.config.settings import AiderSettings
from operations_center.contracts.execution import ExecutionRequest, RuntimeInvocationRef


def _request(tmp_path: Path) -> ExecutionRequest:
    (tmp_path / "repo").mkdir(exist_ok=True)
    return ExecutionRequest(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="hello",
        repo_key="r",
        clone_url="https://example.invalid/r.git",
        base_branch="main",
        task_branch="auto/r",
        workspace_path=tmp_path / "repo",
    )


class _CapturingFakeRuntime:
    """ExecutorRuntime stand-in that records the invocation it received."""

    def __init__(self, *, status: str = "succeeded") -> None:
        self.status = status
        self.last_invocation: RuntimeInvocation | None = None

    def run(self, invocation: RuntimeInvocation) -> RuntimeResult:
        self.last_invocation = invocation
        ar = Path(invocation.artifact_directory) if invocation.artifact_directory else Path("/tmp")
        ar.mkdir(parents=True, exist_ok=True)
        sout = ar / "stdout.txt"
        serr = ar / "stderr.txt"
        sout.write_text("ok", encoding="utf-8")
        serr.write_text("", encoding="utf-8")
        now = datetime.now(timezone.utc).isoformat()
        return RuntimeResult(
            invocation_id=invocation.invocation_id,
            runtime_name=invocation.runtime_name,
            runtime_kind=invocation.runtime_kind,
            status=self.status,
            exit_code=0 if self.status == "succeeded" else 1,
            started_at=now,
            finished_at=now,
            stdout_path=str(sout),
            stderr_path=str(serr),
        )


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestRuntimeInvocationRefHelper:
    def test_builds_ref_from_invocation_and_result(self) -> None:
        inv = RuntimeInvocation(
            invocation_id="iv-1",
            runtime_name="direct_local",
            runtime_kind="subprocess",
            working_directory="/tmp",
            command=["echo"],
            environment={},
            timeout_seconds=10,
            artifact_directory="/tmp/ad",
        )
        rxp_result = RuntimeResult(
            invocation_id="iv-1",
            runtime_name="direct_local",
            runtime_kind="subprocess",
            status="succeeded",
            exit_code=0,
            started_at="2026-05-08T00:00:00+00:00",
            finished_at="2026-05-08T00:00:01+00:00",
            stdout_path="/tmp/ad/stdout.txt",
            stderr_path="/tmp/ad/stderr.txt",
        )
        ref = runtime_invocation_ref(inv, rxp_result)
        assert isinstance(ref, RuntimeInvocationRef)
        assert ref.invocation_id == "iv-1"
        assert ref.runtime_name == "direct_local"
        assert ref.runtime_kind == "subprocess"
        assert ref.stdout_path == "/tmp/ad/stdout.txt"
        assert ref.stderr_path == "/tmp/ad/stderr.txt"
        assert ref.artifact_directory == "/tmp/ad"

    def test_builds_ref_from_invocation_only(self) -> None:
        """No RuntimeResult yet (e.g. FileNotFoundError before runner ran)."""
        inv = RuntimeInvocation(
            invocation_id="iv-2",
            runtime_name="direct_local",
            runtime_kind="subprocess",
            working_directory="/tmp",
            command=["echo"],
            environment={},
            artifact_directory="/tmp/ad2",
        )
        ref = runtime_invocation_ref(inv)
        assert ref.invocation_id == "iv-2"
        assert ref.stdout_path is None
        assert ref.stderr_path is None
        assert ref.artifact_directory == "/tmp/ad2"


# ---------------------------------------------------------------------------
# direct_local adapter — real path through ExecutorRuntime
# ---------------------------------------------------------------------------


class TestDirectLocalRuntimeInvocationRef:
    def test_success_populates_runtime_invocation_ref(self, tmp_path: Path) -> None:
        runtime = _CapturingFakeRuntime()
        adapter = DirectLocalBackendAdapter(
            AiderSettings(binary="/bin/true", timeout_seconds=10), runtime=runtime,
        )
        result = adapter.execute(_request(tmp_path))

        assert result.runtime_invocation_ref is not None
        ref = result.runtime_invocation_ref
        # Identity invariant: ExecutionResult ref points at the
        # exact RuntimeInvocation that ExecutorRuntime received.
        assert runtime.last_invocation is not None
        assert ref.invocation_id == runtime.last_invocation.invocation_id
        # Schema fields propagated.
        assert ref.runtime_name == "direct_local"
        assert ref.runtime_kind == "subprocess"
        # Linked artifacts are present and resolvable.
        assert ref.stdout_path and Path(ref.stdout_path).exists()
        assert ref.stderr_path and Path(ref.stderr_path).exists()
        assert ref.artifact_directory and Path(ref.artifact_directory).is_dir()

    def test_timeout_still_populates_runtime_invocation_ref(self, tmp_path: Path) -> None:
        runtime = _CapturingFakeRuntime(status="timed_out")
        adapter = DirectLocalBackendAdapter(
            AiderSettings(binary="/bin/true", timeout_seconds=1), runtime=runtime,
        )
        result = adapter.execute(_request(tmp_path))

        assert result.runtime_invocation_ref is not None
        assert (
            result.runtime_invocation_ref.invocation_id
            == runtime.last_invocation.invocation_id  # type: ignore[union-attr]
        )

    def test_binary_missing_still_populates_runtime_invocation_ref(self, tmp_path: Path) -> None:
        class _NotFoundRuntime:
            last_invocation: RuntimeInvocation | None = None

            def run(self, invocation: RuntimeInvocation) -> RuntimeResult:
                self.last_invocation = invocation
                raise FileNotFoundError("aider")

        runtime = _NotFoundRuntime()
        adapter = DirectLocalBackendAdapter(
            AiderSettings(binary="/no/such/aider", timeout_seconds=10), runtime=runtime,
        )
        result = adapter.execute(_request(tmp_path))

        assert result.runtime_invocation_ref is not None
        # No RuntimeResult was produced, but the ref still carries
        # invocation identity for audit.
        assert (
            result.runtime_invocation_ref.invocation_id
            == runtime.last_invocation.invocation_id  # type: ignore[union-attr]
        )
        assert result.runtime_invocation_ref.stdout_path is None
        assert result.runtime_invocation_ref.stderr_path is None


# ---------------------------------------------------------------------------
# demo_stub — does not invoke ExecutorRuntime, must leave ref None
# ---------------------------------------------------------------------------


class TestDemoStubLeavesRuntimeInvocationRefNone:
    def test_demo_stub_runtime_invocation_ref_is_none(self, tmp_path: Path) -> None:
        adapter = DemoStubBackendAdapter()
        result = adapter.execute(_request(tmp_path))
        assert result.runtime_invocation_ref is None
