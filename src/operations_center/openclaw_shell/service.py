# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
openclaw_shell/service.py — OpenClawShellService.

The internal service boundary that the shell bridge calls. This is where
OperatorContext crosses the boundary into the internal architecture.

Responsibilities:
  - Map OperatorContext → PlanningContext
  - Call PlanningService to get ProposalDecisionBundle
  - Build ShellRunHandle from the bundle
  - Derive ShellStatusSummary from ExecutionResult/Record
  - Derive ShellInspectionResult from ExecutionRecord/Trace

What this service does NOT do:
  - Routing policy decisions (SwitchBoard's job)
  - Backend invocation (adapter's job)
  - Canonical contract definition (contracts package's job)
  - Retained record storage (observability's job)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from operations_center.observability.models import ExecutionRecord
from operations_center.observability.service import ExecutionObservabilityService
from operations_center.observability.trace import ExecutionTrace
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.routing.service import PlanningService

if TYPE_CHECKING:
    from operations_center.contracts.execution import ExecutionResult

from .models import (
    OperatorContext,
    ShellInspectionResult,
    ShellRunHandle,
    ShellStatusSummary,
)
from .status import inspection_from_record, status_from_record, status_from_result_only

logger = logging.getLogger(__name__)


class OpenClawShellService:
    """Thin internal service boundary for the OpenClaw outer shell.

    Maps operator-facing inputs to internal services and derives shell-facing
    outputs from canonical internal data. The shell layer starts and ends here.

    Usage::

        svc = OpenClawShellService.default()

        # Plan a run (builds proposal + gets routing decision)
        handle = svc.plan(context)

        # Derive status from a canonical execution result
        summary = svc.summarize_result(result, lane="claude_cli", backend="kodo")

        # Inspect a retained record
        inspection = svc.inspect_record(record, trace)
    """

    def __init__(
        self,
        planning_service: PlanningService,
        observability_service: Optional[ExecutionObservabilityService] = None,
    ) -> None:
        self._planning = planning_service
        self._observability = observability_service or ExecutionObservabilityService.default()

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def plan(self, context: OperatorContext) -> ShellRunHandle:
        """Plan a run through the internal architecture.

        Maps OperatorContext → PlanningContext → PlanningService.plan()
        → ProposalDecisionBundle → ShellRunHandle.

        No execution happens here. This is planning + routing only.
        """
        planning_ctx = _build_planning_context(context)
        bundle = self._planning.plan(planning_ctx)
        handle = _build_run_handle(bundle)
        logger.debug(
            "OpenClawShellService.plan: handle=%s lane=%s backend=%s",
            handle.handle_id[:8],
            handle.selected_lane,
            handle.selected_backend,
        )
        return handle

    def plan_with_summary(
        self, context: OperatorContext
    ) -> tuple[ShellRunHandle, ShellStatusSummary]:
        """Plan a run and return both the handle and a route status summary.

        The status summary at plan time reflects routing intent, not execution
        outcome. Status will be "planned"; success=False until execution completes.
        """
        planning_ctx = _build_planning_context(context)
        bundle = self._planning.plan(planning_ctx)
        handle = _build_run_handle(bundle)
        summary = _build_bundle_status_summary(bundle)
        return handle, summary

    # ------------------------------------------------------------------
    # Status derivation from canonical results
    # ------------------------------------------------------------------

    def summarize_result(
        self,
        result: "ExecutionResult",
        lane: str = "",
        backend: str = "",
    ) -> ShellStatusSummary:
        """Derive a ShellStatusSummary from a canonical ExecutionResult.

        Uses the observability layer to build a full record + trace, then
        derives the shell summary from those.
        """
        record, trace = self._observability.observe(result, backend=backend, lane=lane)
        return status_from_record(record, trace)

    def summarize_result_lightweight(
        self,
        result: "ExecutionResult",
        lane: str = "",
        backend: str = "",
    ) -> ShellStatusSummary:
        """Derive a lightweight ShellStatusSummary without running full observability.

        Use when the full ExecutionObservabilityService is not available or needed.
        Headline and summary are synthesized from the result fields directly.
        """
        return status_from_result_only(result, lane=lane or None, backend=backend or None)

    # ------------------------------------------------------------------
    # Inspection from retained records
    # ------------------------------------------------------------------

    def inspect_record(
        self,
        record: ExecutionRecord,
        trace: ExecutionTrace,
    ) -> ShellInspectionResult:
        """Build a shell inspection result from a retained execution record + trace.

        The record and trace come from the observability layer. This function
        is a pure projection — it does not re-run observability.
        """
        return inspection_from_record(record, trace)

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> "OpenClawShellService":
        """Create with default PlanningService and default observability."""
        return cls(PlanningService.default())

    @classmethod
    def with_stub_routing(
        cls,
        lane: str = "claude_cli",
        backend: str = "kodo",
        confidence: float = 0.9,
    ) -> "OpenClawShellService":
        """Create with a stub routing client — for tests and local dev.

        The stub always returns the specified lane/backend decision.
        """
        from operations_center.contracts.enums import BackendName, LaneName
        from operations_center.contracts.routing import LaneDecision
        from operations_center.routing.client import StubLaneRoutingClient

        decision = LaneDecision(
            proposal_id="stub",
            selected_lane=LaneName(lane),
            selected_backend=BackendName(backend),
            confidence=confidence,
            rationale=f"stub routing: {lane}/{backend}",
        )
        stub_client = StubLaneRoutingClient(decision=decision)
        return cls(PlanningService.with_client(stub_client))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_planning_context(context: OperatorContext) -> PlanningContext:
    """Map OperatorContext to the internal PlanningContext."""
    return PlanningContext(
        goal_text=context.goal_text,
        task_type=context.task_type,
        execution_mode=context.execution_mode,
        repo_key=context.repo_key,
        clone_url=context.clone_url,
        base_branch=context.base_branch,
        risk_level=context.risk_level,
        priority=context.priority,
        constraints_text=context.constraints_text,
        labels=list(context.labels),
        allowed_paths=list(context.allowed_paths),
        timeout_seconds=context.timeout_seconds,
        task_id=context.task_id,
        project_id=context.project_id,
        proposer="openclaw-shell",
    )


def _build_run_handle(bundle: ProposalDecisionBundle) -> ShellRunHandle:
    """Build a ShellRunHandle from a ProposalDecisionBundle."""
    return ShellRunHandle(
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        selected_lane=bundle.decision.selected_lane.value,
        selected_backend=bundle.decision.selected_backend.value,
        routing_confidence=bundle.decision.confidence,
        policy_rule=bundle.decision.policy_rule_matched,
        status="planned",
        summary=bundle.run_summary,
    )


def _build_bundle_status_summary(bundle: ProposalDecisionBundle) -> ShellStatusSummary:
    """Build a plan-time ShellStatusSummary from a ProposalDecisionBundle.

    At plan time there is no execution result yet. Status reflects routing
    intent only.
    """
    from operations_center.contracts.enums import ExecutionStatus

    return ShellStatusSummary(
        run_id=bundle.proposal.proposal_id,  # no run_id yet; use proposal_id as proxy
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.PENDING.value,
        success=False,
        headline=f"PLANNED | {bundle.decision.selected_lane.value} @ {bundle.decision.selected_backend.value}",
        summary=bundle.run_summary,
        selected_lane=bundle.decision.selected_lane.value,
        selected_backend=bundle.decision.selected_backend.value,
    )
