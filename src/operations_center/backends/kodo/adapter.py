# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
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

import json
import logging
from pathlib import Path
from typing import Optional

from operations_center.backends.kodo.runner import KodoAdapter
from operations_center.config.settings import KodoSettings
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.observability.models import BackendDetailRef

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
        result, _ = self.execute_and_capture(request)
        return result

    def execute_and_capture(
        self,
        request: ExecutionRequest,
    ) -> tuple[ExecutionResult, Optional[KodoRunCapture]]:
        """Execute the request and return both the canonical result and raw capture.

        The raw KodoRunCapture gives callers access to backend-native detail
        for by-reference retention through the observability layer.

        Returns:
            (ExecutionResult, KodoRunCapture | None) — capture is None when
            the request was rejected before invocation.
        """
        check = self.supports(request)
        if not check.supported:
            logger.warning(
                "KodoBackendAdapter: request %s not supported: %s",
                request.run_id,
                check.reason,
            )
            return _unsupported_result(request, check), None

        try:
            prepared = map_request(request, kodo_mode=self._kodo_mode)
        except Exception as exc:
            logger.error("KodoBackendAdapter: mapping failed for run %s: %s", request.run_id, exc)
            return _mapping_error_result(request, str(exc)), None

        logger.info(
            "KodoBackendAdapter: invoking kodo for run=%s branch=%s mode=%s",
            request.run_id,
            request.task_branch,
            self._kodo_mode,
        )

        # Honor RuntimeBinding when present — bind it to a Kodo team
        # config and write to .kodo/team.json before invocation. This
        # closes G-001 in code: OC's bound runtime takes effect on the
        # actual kodo subprocess.
        team_override_path: Path | None = None
        binder_label: str | None = None
        if request.runtime_binding is not None:
            from operations_center.executors.kodo.binder import (
                BindError, bind as kodo_bind,
            )
            try:
                selection = kodo_bind(request.runtime_binding)
                binder_label = selection.label
                if selection.team_config is not None:
                    workspace = Path(request.workspace_path)
                    team_override_path = workspace / ".kodo" / "team.json"
                    team_override_path.parent.mkdir(exist_ok=True, parents=True)
                    team_override_path.write_text(
                        json.dumps(selection.team_config, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    logger.info(
                        "KodoBackendAdapter: bound runtime to team=%s for run=%s",
                        binder_label, request.run_id,
                    )
                # G-002 fix: pass the binder's CLI orchestrator string
                # through to the underlying kodo invocation.
                if selection.orchestrator is not None:
                    prepared.orchestrator_override = selection.orchestrator
                    logger.info(
                        "KodoBackendAdapter: bound orchestrator=%s for run=%s",
                        selection.orchestrator, request.run_id,
                    )
            except BindError as exc:
                logger.error(
                    "KodoBackendAdapter: cannot bind RuntimeBinding for run %s: %s",
                    request.run_id, exc,
                )
                return _invocation_error_result(request, f"binder error: {exc}"), None

        try:
            capture = self._invoker.invoke(prepared)
        except Exception as exc:
            logger.error(
                "KodoBackendAdapter: invocation failed for run %s: %s",
                request.run_id,
                exc,
            )
            return _invocation_error_result(request, str(exc)), None
        finally:
            if team_override_path is not None:
                team_override_path.unlink(missing_ok=True)

        logger.info(
            "KodoBackendAdapter: run=%s exit_code=%d duration_ms=%d",
            capture.run_id,
            capture.exit_code,
            capture.duration_ms,
        )

        # Attach observed_runtime to the capture so the coordinator's
        # drift detection can compare bound vs observed. Best-effort:
        # Kodo does not report which model the underlying CLI actually
        # used, so observed = what the binder asserted.
        if request.runtime_binding is not None and binder_label is not None:
            observed = {"kind": request.runtime_binding.kind}
            if request.runtime_binding.model:
                observed["model"] = request.runtime_binding.model
            if request.runtime_binding.provider:
                observed["provider"] = request.runtime_binding.provider
            try:
                setattr(capture, "observed_runtime", observed)
                setattr(capture, "binder_label", binder_label)
            except (AttributeError, TypeError):
                pass

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
        capture: KodoRunCapture,
    ) -> list[BackendDetailRef]:
        """Persist raw kodo detail by reference for observability retention."""
        detail_dir = _detail_dir(Path(request.workspace_path), request.run_id)
        refs: list[BackendDetailRef] = []

        if capture.stdout:
            stdout_path = detail_dir / "kodo-stdout.log"
            stdout_path.write_text(capture.stdout, encoding="utf-8")
            refs.append(
                BackendDetailRef(
                    detail_type="stdout_log",
                    path=str(stdout_path),
                    description="Raw kodo stdout retained by reference.",
                    is_required_for_debug=not capture.succeeded,
                )
            )

        if capture.stderr:
            stderr_path = detail_dir / "kodo-stderr.log"
            stderr_path.write_text(capture.stderr, encoding="utf-8")
            refs.append(
                BackendDetailRef(
                    detail_type="stderr_log",
                    path=str(stderr_path),
                    description="Raw kodo stderr retained by reference.",
                    is_required_for_debug=not capture.succeeded or capture.timeout_hit,
                )
            )

        capture_path = detail_dir / "kodo-run-capture.json"
        payload = {
            "run_id": capture.run_id,
            "exit_code": capture.exit_code,
            "command": list(capture.command),
            "started_at": capture.started_at.isoformat(),
            "finished_at": capture.finished_at.isoformat(),
            "duration_ms": capture.duration_ms,
            "timeout_hit": capture.timeout_hit,
            "artifacts": [
                {
                    "label": artifact.label,
                    "artifact_type": artifact.artifact_type,
                }
                for artifact in capture.artifacts
            ],
        }
        capture_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        refs.append(
            BackendDetailRef(
                detail_type="structured_result",
                path=str(capture_path),
                description="Structured kodo run capture retained by reference.",
                is_required_for_debug=not capture.succeeded,
            )
        )
        return refs

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
        failure_category=FailureReasonCategory.UNSUPPORTED_REQUEST,
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


def _detail_dir(workspace_path: Path, run_id: str) -> Path:
    detail_dir = workspace_path / ".operations_center" / "backend_details" / run_id
    detail_dir.mkdir(parents=True, exist_ok=True)
    return detail_dir
