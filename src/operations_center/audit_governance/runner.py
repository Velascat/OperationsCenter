# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Governed audit runner.

run_governed_audit() is the single safe entry point for full managed audits.
It evaluates governance, optionally calls Phase 6 dispatch, and writes a report.

Only AuditGovernanceDecision.decision == "approved" with a valid AuditManualApproval
(when required) may trigger Phase 6 dispatch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from operations_center.audit_dispatch import (
    ManagedAuditDispatchRequest,
    dispatch_managed_audit,
)

from .budgets import increment_budget_after_dispatch, load_budget_state
from .cooldowns import load_cooldown_state, update_cooldown_after_dispatch
from .errors import ManualApprovalError
from .models import (
    AuditBudgetState,
    AuditCooldownState,
    AuditGovernanceReport,
    AuditGovernanceRequest,
    AuditGovernedRunResult,
    AuditManualApproval,
    BudgetStateSummary,
    CooldownStateSummary,
    DispatchResultSummary,
    GovernanceConfig,
    GovernanceStatus,
)
from .policy import evaluate_governance_policies, make_governance_decision
from .reports import write_governance_report


def _check_approval_request_id(
    approval: AuditManualApproval,
    request: AuditGovernanceRequest,
) -> None:
    """Validate that approval.request_id matches the current request.

    The runner re-evaluates governance internally, so decision_id will differ
    from the one on the approval (computed during a prior evaluate step).
    request_id is the durable binding between request and approval artifacts.
    """
    if approval.request_id != request.request_id:
        raise ManualApprovalError(
            f"approval.request_id {approval.request_id!r} does not match "
            f"request.request_id {request.request_id!r}"
        )


def _make_budget_summary(state: AuditBudgetState | None) -> BudgetStateSummary | None:
    if state is None:
        return None
    return BudgetStateSummary(
        runs_used=state.runs_used,
        max_runs=state.max_runs,
        runs_remaining=state.runs_remaining,
        period_start=state.period_start,
        period_end=state.period_end,
    )


def _make_cooldown_summary(state: AuditCooldownState | None) -> CooldownStateSummary | None:
    if state is None:
        return None
    now = datetime.now(UTC)
    return CooldownStateSummary(
        in_cooldown=state.is_in_cooldown(now),
        cooldown_seconds=state.cooldown_seconds,
        seconds_remaining=state.seconds_remaining(now),
        last_run_at=state.last_run_at,
    )


def run_governed_audit(
    request: AuditGovernanceRequest,
    *,
    approval: AuditManualApproval | None = None,
    governance_config: GovernanceConfig | None = None,
    output_dir: Path | str | None = None,
    config_dir: Path | str | None = None,
    log_dir: Path | str | None = None,
    dispatch_timeout_seconds: float | None = None,
) -> AuditGovernedRunResult:
    """Evaluate governance and optionally call Phase 6 dispatch.

    Flow
    ----
    1. Load budget/cooldown state from state_dir.
    2. Run all policy checks.
    3. Make governance decision.
    4. If decision requires manual approval, validate the provided approval.
    5. If approved (and approval valid when needed): call Phase 6 dispatch.
    6. Update budget/cooldown state only after successful dispatch call.
    7. Write governance report regardless of outcome.
    8. Return AuditGovernedRunResult.

    Parameters
    ----------
    request:
        The validated governance request.
    approval:
        Manual approval artifact. Required when decision.requires_manual_approval is True.
    governance_config:
        Governance configuration. Uses empty defaults if None.
    output_dir:
        Root directory for governance reports. Defaults to
        tools/audit/report/governance relative to working dir.
    config_dir:
        Override for managed repo YAML config directory (passed to dispatch).
    log_dir:
        Override for subprocess log directory (passed to dispatch).
    dispatch_timeout_seconds:
        Hard timeout passed to Phase 6 dispatch.

    Returns
    -------
    AuditGovernedRunResult
        Always returned — dispatch and policy failures are structured results,
        not raised exceptions (except infrastructure errors like unwritable reports).
    """
    cfg = governance_config or GovernanceConfig()
    out = Path(output_dir) if output_dir else Path("tools/audit/report/governance")

    # --- Load state ---
    budget_config = cfg.get_budget_config(request.repo_id, request.audit_type)
    cooldown_config = cfg.get_cooldown_config(request.repo_id, request.audit_type)

    try:
        budget_state: AuditBudgetState | None = load_budget_state(
            cfg.state_dir, request.repo_id, request.audit_type, budget_config
        )
    except Exception:
        budget_state = None  # Non-fatal for evaluation; logged in policy results

    try:
        cooldown_state: AuditCooldownState | None = load_cooldown_state(
            cfg.state_dir, request.repo_id, request.audit_type, cooldown_config
        )
    except Exception:
        cooldown_state = None

    # --- Evaluate policies ---
    policy_results = evaluate_governance_policies(
        request,
        known_repos=cfg.known_repos,
        known_audit_types=cfg.known_audit_types,
        budget_state=budget_state,
        cooldown_state=cooldown_state,
        require_mini_regression_for_urgency=cfg.require_mini_regression_for_urgency,
    )

    decision = make_governance_decision(request, policy_results)

    # --- Resolve whether dispatch is permitted ---
    # Dispatch requires one of:
    #   (a) decision == "approved" (no manual approval needed), OR
    #   (b) decision == "needs_manual_approval" AND a valid approval is provided.
    approval_validated = False
    if decision.requires_manual_approval:
        if approval is None:
            report = AuditGovernanceReport(
                request=request,
                decision=decision,
                policy_results=policy_results,
                governance_status="needs_manual_approval",
                budget_state_summary=_make_budget_summary(budget_state),
                cooldown_state_summary=_make_cooldown_summary(cooldown_state),
            )
            report_path = _write_report_safe(report, out)
            return AuditGovernedRunResult(
                request=request,
                decision=decision,
                governance_status="needs_manual_approval",
                report_path=str(report_path),
            )
        try:
            _check_approval_request_id(approval, request)
            approval_validated = True
        except ManualApprovalError as exc:
            report = AuditGovernanceReport(
                request=request,
                decision=decision,
                policy_results=policy_results,
                governance_status="denied",
                approval=approval,
                budget_state_summary=_make_budget_summary(budget_state),
                cooldown_state_summary=_make_cooldown_summary(cooldown_state),
            )
            report_path = _write_report_safe(report, out)
            from .models import AuditGovernanceDecision as _Dec
            denied_decision = _Dec(
                request_id=decision.request_id,
                repo_id=decision.repo_id,
                audit_type=decision.audit_type,
                decision="denied",
                reasons=[f"Manual approval validation failed: {exc}"],
                policy_results=policy_results,
                requires_manual_approval=True,
            )
            return AuditGovernedRunResult(
                request=request,
                decision=denied_decision,
                governance_status="denied",
                report_path=str(report_path),
            )

    # --- Handle non-approved decisions (when approval is not yet validated) ---
    can_dispatch = (decision.decision == "approved") or approval_validated
    if not can_dispatch:
        status_map: dict[str, str] = {
            "denied": "denied",
            "deferred": "deferred",
            "needs_manual_approval": "needs_manual_approval",
        }
        gov_status = cast(GovernanceStatus, status_map.get(decision.decision, "denied"))
        report = AuditGovernanceReport(
            request=request,
            decision=decision,
            policy_results=policy_results,
            governance_status=gov_status,
            approval=approval,
            budget_state_summary=_make_budget_summary(budget_state),
            cooldown_state_summary=_make_cooldown_summary(cooldown_state),
        )
        report_path = _write_report_safe(report, out)
        return AuditGovernedRunResult(
            request=request,
            decision=decision,
            governance_status=gov_status,
            report_path=str(report_path),
        )

    # --- Approved: call Phase 6 dispatch ---
    dispatch_request = ManagedAuditDispatchRequest(
        repo_id=request.repo_id,
        audit_type=request.audit_type,
        requested_by=request.requested_by,
        correlation_id=request.request_id,
        timeout_seconds=dispatch_timeout_seconds,
        metadata=dict(request.metadata),
    )

    try:
        dispatch_result = dispatch_managed_audit(
            dispatch_request,
            config_dir=config_dir,
            log_dir=log_dir,
        )
    except Exception as exc:
        # Infrastructure-level dispatch failure (config error, lock held, etc.)
        dispatch_summary = DispatchResultSummary(
            run_id=None,
            status="failed",
            error=str(exc),
        )
        report = AuditGovernanceReport(
            request=request,
            decision=decision,
            policy_results=policy_results,
            governance_status="dispatch_failed",
            approval=approval,
            dispatch_result_summary=dispatch_summary,
            budget_state_summary=_make_budget_summary(budget_state),
            cooldown_state_summary=_make_cooldown_summary(cooldown_state),
        )
        report_path = _write_report_safe(report, out)
        return AuditGovernedRunResult(
            request=request,
            decision=decision,
            governance_status="dispatch_failed",
            report_path=str(report_path),
        )

    # --- Update budget/cooldown state after dispatch ---
    ran_at = datetime.now(UTC)
    try:
        budget_state = increment_budget_after_dispatch(
            cfg.state_dir, request.repo_id, request.audit_type, budget_config, ran_at
        )
    except Exception:
        pass  # State update failure is non-fatal; dispatch already ran

    try:
        cooldown_state = update_cooldown_after_dispatch(
            cfg.state_dir, request.repo_id, request.audit_type, cooldown_config, ran_at
        )
    except Exception:
        pass

    dispatch_summary = DispatchResultSummary(
        run_id=dispatch_result.run_id,
        status=dispatch_result.status.value,
        failure_kind=dispatch_result.failure_kind.value if dispatch_result.failure_kind else None,
        duration_seconds=dispatch_result.duration_seconds,
        artifact_manifest_path=dispatch_result.artifact_manifest_path,
        error=dispatch_result.error,
    )

    report = AuditGovernanceReport(
        request=request,
        decision=decision,
        policy_results=policy_results,
        governance_status="approved_and_dispatched",
        approval=approval,
        dispatch_result_summary=dispatch_summary,
        budget_state_summary=_make_budget_summary(budget_state),
        cooldown_state_summary=_make_cooldown_summary(cooldown_state),
    )
    report_path = _write_report_safe(report, out)

    return AuditGovernedRunResult(
        request=request,
        decision=decision,
        governance_status="approved_and_dispatched",
        dispatch_result=dispatch_result,
        report_path=str(report_path),
    )


def _write_report_safe(report: AuditGovernanceReport, output_dir: Path) -> Path:
    """Write report, raising GovernanceReportError on failure."""
    return write_governance_report(report, output_dir)


__all__ = ["run_governed_audit"]
