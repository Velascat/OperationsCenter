# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""
openclaw_shell/bridge.py — OpenClawBridge.

The explicit crossing point between the OpenClaw outer shell and the
internal contract-owned architecture.

The bridge enforces the integration posture:
  - OpenClaw calls inward through this boundary
  - Internal normalized results come back outward
  - No internal logic leaks into the shell layer
  - No OpenClaw-native event semantics leak inward

Why a bridge and not direct service calls?
  - Makes the crossing point inspectable in code and tests
  - Easier to stub the entire integration in test suites
  - Makes optionality obvious: nothing inside the core architecture
    imports from bridge.py

Usage::

    bridge = OpenClawBridge.default()

    # Trigger a run (planning + routing)
    handle = bridge.trigger(context)

    # Get status from execution result
    summary = bridge.status_from_result(result)

    # Inspect a retained record
    inspection = bridge.inspect_from_record(record, trace)

    # Check if OpenClaw shell is enabled
    if OpenClawBridge.is_enabled():
        bridge = OpenClawBridge.default()
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from operations_center.observability.models import ExecutionRecord
from operations_center.observability.trace import ExecutionTrace

from .models import (
    OperatorContext,
    ShellActionResult,
    ShellInspectionResult,
    ShellRunHandle,
    ShellStatusSummary,
)
from .service import OpenClawShellService

if TYPE_CHECKING:
    from operations_center.contracts.execution import ExecutionResult

logger = logging.getLogger(__name__)

# Environment variable that enables/disables the OpenClaw shell.
# The system runs without OpenClaw by default; set this to "1" to enable.
_OPENCLAW_SHELL_ENV_VAR = "OPENCLAW_SHELL_ENABLED"


class OpenClawBridge:
    """Explicit integration boundary for the OpenClaw outer shell.

    The bridge is the only point at which OpenClaw shell calls cross into
    the internal architecture. All inputs enter as shell-facing types
    (OperatorContext); all outputs return as shell-facing summaries
    (ShellRunHandle, ShellStatusSummary, ShellInspectionResult).

    The internal architecture's types (ProposalDecisionBundle, ExecutionResult,
    ExecutionRecord, ExecutionTrace) never need to cross outward beyond the
    bridge boundary in normal operator use.
    """

    def __init__(self, service: OpenClawShellService) -> None:
        self._service = service

    # ------------------------------------------------------------------
    # Trigger
    # ------------------------------------------------------------------

    def trigger(self, context: OperatorContext) -> ShellRunHandle:
        """Trigger a run through the outer shell.

        Calls the internal planning pipeline (OperationsCenter → SwitchBoard)
        and returns a handle for the planned run. Execution is NOT started
        here — the handle contains the information needed to start it.
        """
        logger.info(
            "OpenClawBridge.trigger: repo=%s task_type=%s risk=%s",
            context.repo_key,
            context.task_type,
            context.risk_level,
        )
        return self._service.plan(context)

    def trigger_with_summary(
        self, context: OperatorContext
    ) -> tuple[ShellRunHandle, ShellStatusSummary]:
        """Trigger and return both handle and route status summary."""
        return self._service.plan_with_summary(context)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status_from_result(
        self,
        result: "ExecutionResult",
        lane: str = "",
        backend: str = "",
    ) -> ShellStatusSummary:
        """Derive shell status from a canonical ExecutionResult.

        Uses the observability layer internally to build a full record + trace.
        """
        return self._service.summarize_result(result, lane=lane, backend=backend)

    def status_from_result_lightweight(
        self,
        result: "ExecutionResult",
        lane: str = "",
        backend: str = "",
    ) -> ShellStatusSummary:
        """Derive a lightweight shell status without full observability.

        Use when a minimal summary is sufficient and full record building
        is unnecessary.
        """
        return self._service.summarize_result_lightweight(result, lane=lane, backend=backend)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def inspect_from_record(
        self,
        record: ExecutionRecord,
        trace: ExecutionTrace,
    ) -> ShellInspectionResult:
        """Inspect a retained execution record through the shell lens."""
        return self._service.inspect_record(record, trace)

    # ------------------------------------------------------------------
    # Shell action result helpers
    # ------------------------------------------------------------------

    def wrap_action(
        self,
        action: str,
        fn,
        *args,
        **kwargs,
    ) -> ShellActionResult:
        """Execute a callable and wrap the outcome in a ShellActionResult.

        Catches exceptions and returns them as failed actions rather than
        propagating them outward. Keeps the shell boundary clean for
        operator-facing code.
        """
        try:
            fn(*args, **kwargs)
            return ShellActionResult(action=action, success=True, message="ok")
        except Exception as exc:
            logger.warning("OpenClawBridge action '%s' failed: %s", action, exc)
            return ShellActionResult(
                action=action,
                success=False,
                message=str(exc),
                detail=type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # Optionality
    # ------------------------------------------------------------------

    @classmethod
    def is_enabled(cls) -> bool:
        """Return True if the OpenClaw shell integration is enabled.

        Enabled when OPENCLAW_SHELL_ENABLED=1 is set in the environment.
        The system works fully without this enabled — this flag governs
        whether the shell layer is active, not whether the core works.
        """
        return os.environ.get(_OPENCLAW_SHELL_ENV_VAR, "0").strip() == "1"

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> "OpenClawBridge":
        """Create with default internal services."""
        return cls(OpenClawShellService.default())

    @classmethod
    def with_stub_routing(
        cls,
        lane: str = "claude_cli",
        backend: str = "kodo",
        confidence: float = 0.9,
    ) -> "OpenClawBridge":
        """Create with a stub routing client — for tests and local dev."""
        return cls(
            OpenClawShellService.with_stub_routing(
                lane=lane, backend=backend, confidence=confidence
            )
        )
