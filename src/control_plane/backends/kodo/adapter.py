"""
backends/kodo/adapter.py — KodoBackendAdapter: canonical entry point.

KodoBackendAdapter is the Phase 5 public boundary for kodo backend execution.

    ExecutionRequest → KodoBackendAdapter.execute() → ExecutionResult

The adapter orchestrates:
  1. Support check (is this request kodo-compatible?)
  2. Mapping (ExecutionRequest → KodoPreparedRun)
  3. Invocation (KodoPreparedRun → KodoRunCapture via KodoBackendInvoker)
  4. Normalization (KodoRunCapture → ExecutionResult)

It does not implement routing policy, task proposal logic, or model hosting.
It does not expose kodo-native types outside this module.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from control_plane.adapters.kodo.adapter import KodoAdapter
from control_plane.config.settings import KodoSettings
from control_plane.contracts.execution import ExecutionRequest, ExecutionResult
from control_plane.contracts.enums import ExecutionStatus, FailureReasonCategory

from .invoke import KodoBackendInvoker
from .mapper import check_support, map_request
from .models import KodoRunCapture, SupportCheck
from .normalize import normalize

logger = logging.getLogger(__name__)


class KodoBackendAdapter:
    """Canonical adapter for kodo backend execution.

    Public boundary: accepts ExecutionRequest, returns ExecutionResult.
    All kodo-native types are contained inside this module.

    Usage::

        kodo_raw = KodoAdapter(KodoSettings())
        adapter = KodoBackendAdapter(kodo_raw)

        check = adapter.supports(request)
        if check.supported:
            result = adapter.execute(request)
    """

    def __init__(
        self,
        kodo_adapter: KodoAdapter,
        kodo_mode: str = "goal",
    ) -> None:
        self._invoker = KodoBackendInvoker(kodo_adapter)
        self._kodo_mode = kodo_mode

    def supports(self, request: ExecutionRequest) -> SupportCheck:
        """Check whether this adapter can handle the given request."""
        return check_support(request)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute the request via kodo and return a canonical ExecutionResult.

        Steps:
          1. Support check — returns a FAILED result immediately if unsupported.
          2. Map request → KodoPreparedRun.
          3. Invoke kodo → KodoRunCapture.
          4. Normalize → ExecutionResult.
        """
        check = self.supports(request)
        if not check.supported:
            logger.warning(
                "KodoBackendAdapter: request %s not supported: %s",
                request.run_id,
                check.reason,
            )
            return _unsupported_result(request, check)

        try:
            prepared = map_request(request, kodo_mode=self._kodo_mode)
        except Exception as exc:
            logger.error("KodoBackendAdapter: mapping failed for run %s: %s", request.run_id, exc)
            return _mapping_error_result(request, str(exc))

        logger.info(
            "KodoBackendAdapter: invoking kodo for run=%s branch=%s mode=%s",
            request.run_id,
            request.task_branch,
            self._kodo_mode,
        )

        try:
            capture = self._invoker.invoke(prepared)
        except Exception as exc:
            logger.error(
                "KodoBackendAdapter: invocation failed for run %s: %s",
                request.run_id,
                exc,
            )
            return _invocation_error_result(request, str(exc))

        logger.info(
            "KodoBackendAdapter: run=%s exit_code=%d duration_ms=%d",
            capture.run_id,
            capture.exit_code,
            capture.duration_ms,
        )

        return normalize(
            capture=capture,
            proposal_id=request.proposal_id,
            decision_id=request.decision_id,
            branch_name=request.task_branch,
            workspace_path=Path(request.workspace_path),
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        settings: Optional[KodoSettings] = None,
        kodo_mode: str = "goal",
    ) -> "KodoBackendAdapter":
        """Convenience factory using KodoSettings."""
        kodo_settings = settings or KodoSettings()
        return cls(
            kodo_adapter=KodoAdapter(kodo_settings),
            kodo_mode=kodo_mode,
        )


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
        failure_reason=f"Request not supported by kodo adapter: {check.reason}",
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
        failure_reason=f"kodo invocation failed: {error}",
    )
