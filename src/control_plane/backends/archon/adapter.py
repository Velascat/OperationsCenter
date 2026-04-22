"""
backends/archon/adapter.py — ArchonBackendAdapter: canonical entry point.

ArchonBackendAdapter is the Phase 8 public boundary for Archon backend execution.

    ExecutionRequest → ArchonBackendAdapter.execute() → ExecutionResult

The adapter orchestrates:
  1. Support check (is this request Archon-compatible?)
  2. Mapping (ExecutionRequest → ArchonWorkflowConfig)
  3. Invocation (ArchonWorkflowConfig → ArchonRunCapture via ArchonBackendInvoker)
  4. Normalization (ArchonRunCapture → ExecutionResult)

Archon is optional and bounded. The adapter does not implement routing policy,
task proposal logic, local lane hosting, or any canonical schema modification.
Archon-native workflow types do not escape this module.

Use execute_and_capture() when the caller needs the raw ArchonRunCapture
(e.g. to extract workflow_events for BackendDetailRef retention via the
observability layer). The canonical execute() interface returns only the
canonical ExecutionResult.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from control_plane.contracts.enums import ExecutionStatus, FailureReasonCategory
from control_plane.contracts.execution import ExecutionRequest, ExecutionResult

from .invoke import ArchonAdapter, ArchonBackendInvoker
from .mapper import check_support, map_request
from .models import ArchonRunCapture, SupportCheck
from .normalize import normalize

logger = logging.getLogger(__name__)


class ArchonBackendAdapter:
    """Canonical adapter for Archon backend execution.

    Public boundary: accepts ExecutionRequest, returns ExecutionResult.
    All Archon-native types are contained inside this module.

    Archon is optional — check supports() before calling execute() in contexts
    where multiple backends may be available.

    Usage::

        archon_raw = ConcreteArchonAdapter(...)  # your ArchonAdapter subclass
        adapter = ArchonBackendAdapter(archon_raw)

        check = adapter.supports(request)
        if check.supported:
            result = adapter.execute(request)

    To also retain raw workflow events::

        result, capture = adapter.execute_and_capture(request)
        if capture:
            # capture.workflow_events → retain as BackendDetailRef
    """

    def __init__(
        self,
        archon_adapter: ArchonAdapter,
        switchboard_url: str = "",
        workflow_type: str = "goal",
    ) -> None:
        self._invoker = ArchonBackendInvoker(archon_adapter, switchboard_url=switchboard_url)
        self._workflow_type = workflow_type

    def supports(self, request: ExecutionRequest) -> SupportCheck:
        """Check whether this adapter can handle the given request."""
        return check_support(request)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute the request via Archon and return a canonical ExecutionResult.

        Steps:
          1. Support check — returns a FAILED result immediately if unsupported.
          2. Map request → ArchonWorkflowConfig.
          3. Invoke Archon → ArchonRunCapture.
          4. Normalize → ExecutionResult.
        """
        result, _ = self.execute_and_capture(request)
        return result

    def execute_and_capture(
        self,
        request: ExecutionRequest,
    ) -> tuple[ExecutionResult, Optional[ArchonRunCapture]]:
        """Execute the request and return both the canonical result and raw capture.

        The raw ArchonRunCapture gives access to workflow_events for callers
        who want to retain raw Archon detail as BackendDetailRef entries via
        the observability layer.

        Returns:
            (ExecutionResult, ArchonRunCapture | None) — capture is None when
            the request was rejected before invocation.
        """
        check = self.supports(request)
        if not check.supported:
            logger.warning(
                "ArchonBackendAdapter: request %s not supported: %s",
                request.run_id,
                check.reason,
            )
            return _unsupported_result(request, check), None

        try:
            prepared = map_request(request, workflow_type=self._workflow_type)
        except Exception as exc:
            logger.error(
                "ArchonBackendAdapter: mapping failed for run %s: %s",
                request.run_id,
                exc,
            )
            return _mapping_error_result(request, str(exc)), None

        logger.info(
            "ArchonBackendAdapter: invoking archon for run=%s branch=%s workflow=%s",
            request.run_id,
            request.task_branch,
            self._workflow_type,
        )

        try:
            capture = self._invoker.invoke(prepared)
        except Exception as exc:
            logger.error(
                "ArchonBackendAdapter: invocation failed for run %s: %s",
                request.run_id,
                exc,
            )
            return _invocation_error_result(request, str(exc)), None

        logger.info(
            "ArchonBackendAdapter: run=%s outcome=%s duration_ms=%d events=%d",
            capture.run_id,
            capture.outcome,
            capture.duration_ms,
            len(capture.workflow_events),
        )

        result = normalize(
            capture=capture,
            proposal_id=request.proposal_id,
            decision_id=request.decision_id,
            branch_name=request.task_branch,
            workspace_path=Path(request.workspace_path),
        )
        return result, capture

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def with_stub(
        cls,
        outcome: str = "success",
        output_text: str = "",
        error_text: str = "",
        workflow_events: Optional[list[dict]] = None,
    ) -> "ArchonBackendAdapter":
        """Convenience factory for tests using a StubArchonAdapter."""
        from .invoke import ArchonRunResult, StubArchonAdapter

        stub = StubArchonAdapter(
            ArchonRunResult(
                outcome=outcome,
                exit_code=0 if outcome == "success" else 1,
                output_text=output_text,
                error_text=error_text,
                workflow_events=workflow_events or [],
            )
        )
        return cls(archon_adapter=stub)


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
        failure_category=FailureReasonCategory.POLICY_BLOCKED,
        failure_reason=f"Request not supported by Archon adapter: {check.reason}",
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
        failure_reason=f"Archon invocation failed: {error}",
    )
