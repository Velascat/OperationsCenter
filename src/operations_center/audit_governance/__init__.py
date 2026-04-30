# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 12: Full audit governance subsystem.

Public API:
  run_governed_audit()         — evaluate governance + optional Phase 6 dispatch
  evaluate_governance_policies() — run policy checks (pure, no side effects)
  make_governance_decision()   — build decision from policy results
  write_governance_report()    — persist a governance report
  load_governance_report()     — load a persisted governance report
  make_manual_approval()       — create and validate a manual approval artifact
  validate_manual_approval()   — validate an existing approval artifact
"""

from .approvals import make_manual_approval, validate_manual_approval
from .budgets import increment_budget_after_dispatch, load_budget_state, save_budget_state
from .cooldowns import load_cooldown_state, save_cooldown_state, update_cooldown_after_dispatch
from .errors import (
    AuditGovernanceError,
    BudgetStateError,
    CooldownStateError,
    GovernancePolicyError,
    GovernanceReportError,
    GovernanceRequestError,
    ManualApprovalError,
)
from .models import (
    AuditBudgetState,
    AuditCooldownState,
    AuditGovernanceDecision,
    AuditGovernanceReport,
    AuditGovernanceRequest,
    AuditGovernedRunResult,
    AuditManualApproval,
    BudgetConfig,
    BudgetStateSummary,
    CooldownConfig,
    CooldownStateSummary,
    DispatchResultSummary,
    GovernanceConfig,
    PolicyResult,
    make_request_id,
)
from .policy import evaluate_governance_policies, make_governance_decision
from .reports import load_governance_report, write_governance_report
from .runner import run_governed_audit

__all__ = [
    "AuditBudgetState",
    "AuditCooldownState",
    "AuditGovernanceDecision",
    "AuditGovernanceError",
    "AuditGovernanceReport",
    "AuditGovernanceRequest",
    "AuditGovernedRunResult",
    "AuditManualApproval",
    "BudgetConfig",
    "BudgetStateError",
    "BudgetStateSummary",
    "CooldownConfig",
    "CooldownStateError",
    "CooldownStateSummary",
    "DispatchResultSummary",
    "GovernanceConfig",
    "GovernancePolicyError",
    "GovernanceReportError",
    "GovernanceRequestError",
    "ManualApprovalError",
    "PolicyResult",
    "evaluate_governance_policies",
    "increment_budget_after_dispatch",
    "load_budget_state",
    "load_cooldown_state",
    "load_governance_report",
    "make_governance_decision",
    "make_manual_approval",
    "make_request_id",
    "run_governed_audit",
    "save_budget_state",
    "save_cooldown_state",
    "update_cooldown_after_dispatch",
    "validate_manual_approval",
    "write_governance_report",
]
