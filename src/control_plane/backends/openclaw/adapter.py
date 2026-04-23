"""
backends/openclaw/adapter.py — OpenClawBackendAdapter: canonical entry point.

OpenClawBackendAdapter is the Phase 11 public boundary for OpenClaw backend execution.

    ExecutionRequest → OpenClawBackendAdapter.execute() → ExecutionResult

The adapter orchestrates:
  1. Support check (is this request OpenClaw-compatible?)
  2. Mapping (ExecutionRequest → OpenClawPreparedRun)
  3. Invocation (OpenClawPreparedRun → OpenClawRunCapture via OpenClawBackendInvoker)
  4. Normalization (OpenClawRunCapture → ExecutionResult)

OpenClaw is optional and bounded. The adapter does not implement routing policy,
task proposal logic, local lane hosting, or any canonical schema modification.
OpenClaw-native event types do not escape this module.

IMPORTANT: This is the backend adapter role. It is separate from the outer-shell
role (openclaw_shell/ in Phase 10). The outer shell provides an optional operator
interface; this adapter provides backend execution behind the canonical contracts.
They must not be collapsed.

Use execute_and_capture() when the caller needs the raw OpenClawRunCapture
(e.g. to extract events for BackendDetailRef retention via the observability
layer). The canonical execute() interface returns only the canonical ExecutionResult.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from control_plane.contracts.enums import ExecutionStatus, FailureReasonCategory
from control_plane.contracts.execution import ExecutionRequest, ExecutionResult
from control_plane.observability.models import BackendDetailRef

from .invoke import OpenClawBackendInvoker, OpenClawRunner
from .mapper import check_support, map_request
from .models import OpenClawRunCapture, SupportCheck
from .normalize import normalize

logger = logging.getLogger(__name__)


class OpenClawBackendAdapter:
    """Canonical adapter for OpenClaw backend execution.

    Public boundary: accepts ExecutionRequest, returns ExecutionResult.
    All OpenClaw-native types are contained inside this module.

    OpenClaw is optional — check supports() before calling execute() in contexts
    where multiple backends may be available.

    This is the BACKEND ADAPTER role. It is distinct from the outer-shell role
    (Phase 10 openclaw_shell/). Do not conflate the two.

    Usage::

        runner = ConcreteOpenClawRunner(...)  # your OpenClawRunner subclass
        adapter = OpenClawBackendAdapter(runner)

        check = adapter.supports(request)
        if check.supported:
            result = adapter.execute(request)

    To also retain raw OpenClaw events::

        result, capture = adapter.execute_and_capture(request)
        if capture:
            # capture.events → retain as BackendDetailRef
            # capture.changed_files_source → "git_diff" | "event_stream" | "unknown"
    """

    def __init__(
        self,
        runner: OpenClawRunner,
        run_mode: str = "goal",
    ) -> None:
        self._invoker = OpenClawBackendInvoker(runner)
        self._run_mode = run_mode

    def supports(self, request: ExecutionRequest) -> SupportCheck:
        """Check whether this adapter can handle the given request."""
        return check_support(request)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute the request via OpenClaw and return a canonical ExecutionResult.

        Steps:
          1. Support check — returns a FAILED result immediately if unsupported.
          2. Map request → OpenClawPreparedRun.
          3. Invoke OpenClaw → OpenClawRunCapture.
          4. Normalize → ExecutionResult.
        """
        result, _ = self.execute_and_capture(request)
        return result

    def execute_and_capture(
        self,
        request: ExecutionRequest,
    ) -> tuple[ExecutionResult, Optional[OpenClawRunCapture]]:
        """Execute the request and return both the canonical result and raw capture.

        The raw OpenClawRunCapture gives access to events for callers who want to
        retain raw OpenClaw detail as BackendDetailRef entries via the observability
        layer.

        The capture also carries changed_files_source so callers can represent
        changed-file evidence honestly in retained records.

        Returns:
            (ExecutionResult, OpenClawRunCapture | None) — capture is None when
            the request was rejected before invocation.
        """
        check = self.supports(request)
        if not check.supported:
            logger.warning(
                "OpenClawBackendAdapter: request %s not supported: %s",
                request.run_id,
                check.reason,
            )
            return _unsupported_result(request, check), None

        try:
            prepared = map_request(request, run_mode=self._run_mode)
        except Exception as exc:
            logger.error(
                "OpenClawBackendAdapter: mapping failed for run %s: %s",
                request.run_id,
                exc,
            )
            return _mapping_error_result(request, str(exc)), None

        logger.info(
            "OpenClawBackendAdapter: invoking openclaw for run=%s branch=%s mode=%s",
            request.run_id,
            request.task_branch,
            self._run_mode,
        )

        try:
            capture = self._invoker.invoke(prepared)
        except Exception as exc:
            logger.error(
                "OpenClawBackendAdapter: invocation failed for run %s: %s",
                request.run_id,
                exc,
            )
            return _invocation_error_result(request, str(exc)), None

        logger.info(
            "OpenClawBackendAdapter: run=%s outcome=%s duration_ms=%d events=%d source=%s",
            capture.run_id,
            capture.outcome,
            capture.duration_ms,
            capture.event_count,
            capture.changed_files_source,
        )

        result = normalize(
            capture=capture,
            proposal_id=request.proposal_id,
            decision_id=request.decision_id,
            branch_name=request.task_branch,
            workspace_path=Path(request.workspace_path),
        )
        return result, capture

    def build_backend_detail_refs(
        self,
        request: ExecutionRequest,
        capture: OpenClawRunCapture,
    ) -> list[BackendDetailRef]:
        """Persist raw OpenClaw detail by reference for observability retention."""
        detail_dir = _detail_dir(Path(request.workspace_path), request.run_id)
        refs: list[BackendDetailRef] = []

        if capture.events:
            events_path = detail_dir / "openclaw-events.json"
            events_path.write_text(json.dumps(capture.events, indent=2) + "\n", encoding="utf-8")
            refs.append(
                BackendDetailRef(
                    detail_type="event_trace",
                    path=str(events_path),
                    description="Raw OpenClaw event stream retained by reference.",
                    is_required_for_debug=True,
                )
            )

        capture_path = detail_dir / "openclaw-run-capture.json"
        payload = {
            "run_id": capture.run_id,
            "outcome": capture.outcome,
            "exit_code": capture.exit_code,
            "duration_ms": capture.duration_ms,
            "timeout_hit": capture.timeout_hit,
            "changed_files_source": capture.changed_files_source,
            "reported_changed_files": capture.reported_changed_files,
            "output_text": capture.output_text,
            "error_text": capture.error_text,
        }
        capture_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        refs.append(
            BackendDetailRef(
                detail_type="structured_result",
                path=str(capture_path),
                description="Structured OpenClaw capture retained by reference.",
                is_required_for_debug=(not capture.succeeded) or capture.changed_files_source != "git_diff",
            )
        )
        return refs

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def with_stub(
        cls,
        outcome: str = "success",
        output_text: str = "",
        error_text: str = "",
        events: Optional[list[dict]] = None,
        reported_changed_files: Optional[list[dict]] = None,
    ) -> "OpenClawBackendAdapter":
        """Convenience factory for tests using a StubOpenClawRunner."""
        from .invoke import OpenClawRunResult, StubOpenClawRunner

        stub = StubOpenClawRunner(
            OpenClawRunResult(
                outcome=outcome,
                exit_code=0 if outcome == "success" else 1,
                output_text=output_text,
                error_text=error_text,
                events=events or [],
                reported_changed_files=reported_changed_files or [],
            )
        )
        return cls(runner=stub)


# ---------------------------------------------------------------------------
# Error result builders
# ---------------------------------------------------------------------------

def _unsupported_result(request: ExecutionRequest, check: SupportCheck) -> ExecutionResult:
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.UNSUPPORTED_REQUEST,
        failure_reason=f"Request not supported by OpenClaw adapter: {check.reason}",
    )


def _mapping_error_result(request: ExecutionRequest, error: str) -> ExecutionResult:
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason=f"Request mapping failed: {error}",
    )


def _invocation_error_result(request: ExecutionRequest, error: str) -> ExecutionResult:
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason=f"openclaw invocation failed: {error}",
    )


def _detail_dir(workspace_path: Path, run_id: str) -> Path:
    detail_dir = workspace_path / ".control_plane" / "backend_details" / run_id
    detail_dir.mkdir(parents=True, exist_ok=True)
    return detail_dir
