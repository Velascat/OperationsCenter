# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Manual approval handling for audit governance.

validate_manual_approval() checks that an approval artifact consistently
references its decision and request.
"""

from __future__ import annotations

from .errors import ManualApprovalError
from .models import AuditGovernanceDecision, AuditGovernanceRequest, AuditManualApproval


def validate_manual_approval(
    approval: AuditManualApproval,
    decision: AuditGovernanceDecision,
    request: AuditGovernanceRequest,
) -> None:
    """Validate that an approval artifact correctly references decision and request.

    Raises
    ------
    ManualApprovalError
        If approval.decision_id or approval.request_id do not match the provided objects,
        or if the decision is not in a state that accepts manual approval.
    """
    if approval.decision_id != decision.decision_id:
        raise ManualApprovalError(
            f"approval.decision_id {approval.decision_id!r} does not match "
            f"decision.decision_id {decision.decision_id!r}"
        )
    if approval.request_id != request.request_id:
        raise ManualApprovalError(
            f"approval.request_id {approval.request_id!r} does not match "
            f"request.request_id {request.request_id!r}"
        )
    if decision.decision in ("denied",):
        raise ManualApprovalError(
            f"Cannot approve a denied decision (decision={decision.decision!r}). "
            "A new governance request is required."
        )


def make_manual_approval(
    decision: AuditGovernanceDecision,
    request: AuditGovernanceRequest,
    *,
    approved_by: str,
    approval_notes: str = "",
) -> AuditManualApproval:
    """Create and validate a manual approval for a decision.

    Raises ManualApprovalError if the request/decision state is invalid.
    """
    approval = AuditManualApproval(
        decision_id=decision.decision_id,
        request_id=request.request_id,
        approved_by=approved_by,
        approval_notes=approval_notes,
    )
    validate_manual_approval(approval, decision, request)
    return approval


__all__ = [
    "make_manual_approval",
    "validate_manual_approval",
]
