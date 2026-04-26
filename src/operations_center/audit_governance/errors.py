"""Errors for the audit governance subsystem."""

from __future__ import annotations


class AuditGovernanceError(Exception):
    """Base error for the audit governance subsystem."""


class GovernanceRequestError(AuditGovernanceError):
    """Invalid or malformed governance request."""


class GovernancePolicyError(AuditGovernanceError):
    """Internal error during policy evaluation (not a policy failure)."""


class GovernanceReportError(AuditGovernanceError):
    """Failure writing or loading a governance report."""


class BudgetStateError(AuditGovernanceError):
    """Failure reading or writing budget state."""


class CooldownStateError(AuditGovernanceError):
    """Failure reading or writing cooldown state."""


class ManualApprovalError(AuditGovernanceError):
    """Invalid manual approval — missing required fields or mismatched references."""


__all__ = [
    "AuditGovernanceError",
    "BudgetStateError",
    "CooldownStateError",
    "GovernancePolicyError",
    "GovernanceReportError",
    "GovernanceRequestError",
    "ManualApprovalError",
]
