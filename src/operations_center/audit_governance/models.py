"""Phase 12 audit governance models.

Pydantic frozen models for durable artifacts (request, decision, approval, report).
Plain dataclasses for configuration objects.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from operations_center.audit_dispatch.models import ManagedAuditDispatchResult

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

AuditUrgency = Literal["low", "normal", "high", "urgent"]
GovernanceDecisionValue = Literal["approved", "denied", "needs_manual_approval", "deferred"]
PolicyStatus = Literal["passed", "failed", "warning", "skipped"]
GovernanceStatus = Literal[
    "approved_and_dispatched",
    "denied",
    "deferred",
    "needs_manual_approval",
    "dispatch_failed",
]

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_\-]")


def _safe_id(raw: str) -> str:
    return _SAFE_ID_RE.sub("_", raw)


def _make_request_id(repo_id: str, audit_type: str) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    raw = f"{repo_id}__{audit_type}__{ts}"
    return _safe_id(raw)


# ---------------------------------------------------------------------------
# Policy result (one check)
# ---------------------------------------------------------------------------

class PolicyResult(BaseModel, frozen=True):
    """Result of a single governance policy check."""

    policy_name: str
    status: PolicyStatus
    reason: str
    details: str = ""


# ---------------------------------------------------------------------------
# Governance request (serializable — Pydantic for validation)
# ---------------------------------------------------------------------------

class AuditGovernanceRequest(BaseModel, frozen=True):
    """A request to run a full managed audit under governance.

    Recommendations may appear as related_recommendation_ids for context only.
    They cannot approve or execute a request.
    """

    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()).replace("-", "_"),
        description="Unique, path-safe identifier.",
    )
    repo_id: str = Field(description="Managed repo identifier.")
    audit_type: str = Field(description="Audit type to run.")
    requested_by: str = Field(description="Operator identity requesting the audit.")
    requested_reason: str = Field(description="Non-empty explanation of why the audit is needed.")
    urgency: AuditUrgency = "normal"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Evidence context — informational, never approval
    related_suite_report_path: str | None = None
    related_calibration_report_path: str | None = None
    related_recommendation_ids: list[str] = Field(default_factory=list)
    requested_time_window: str | None = None
    allow_if_recent_success: bool = False

    @field_validator("repo_id")
    @classmethod
    def _repo_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("repo_id must not be empty")
        return v

    @field_validator("audit_type")
    @classmethod
    def _audit_type_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("audit_type must not be empty")
        return v

    @field_validator("requested_reason")
    @classmethod
    def _reason_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("requested_reason must not be empty")
        return v

    @field_validator("requested_by")
    @classmethod
    def _requested_by_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("requested_by must not be empty")
        return v


# ---------------------------------------------------------------------------
# Governance decision
# ---------------------------------------------------------------------------

class AuditGovernanceDecision(BaseModel, frozen=True):
    """Result of evaluating a governance request against all policies."""

    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str
    repo_id: str
    audit_type: str
    decision: GovernanceDecisionValue
    reasons: list[str]
    policy_results: list[PolicyResult]
    requires_manual_approval: bool
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_approved(self) -> bool:
        return self.decision == "approved"

    @property
    def is_denied(self) -> bool:
        return self.decision == "denied"

    @property
    def failed_policies(self) -> list[PolicyResult]:
        return [p for p in self.policy_results if p.status == "failed"]

    @property
    def warning_policies(self) -> list[PolicyResult]:
        return [p for p in self.policy_results if p.status == "warning"]


# ---------------------------------------------------------------------------
# Manual approval
# ---------------------------------------------------------------------------

class AuditManualApproval(BaseModel, frozen=True):
    """Human approval record for a governance decision that requires manual sign-off.

    This is a data artifact, not code execution.
    It does not bypass known-repo or known-audit-type validation.
    """

    approval_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    decision_id: str = Field(description="ID of the AuditGovernanceDecision being approved.")
    request_id: str = Field(description="ID of the original AuditGovernanceRequest.")
    approved_by: str = Field(description="Human operator granting approval.")
    approved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approval_notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("approved_by")
    @classmethod
    def _approved_by_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("approved_by must not be empty")
        return v


# ---------------------------------------------------------------------------
# Budget and cooldown state (file-backed JSON)
# ---------------------------------------------------------------------------

class AuditBudgetState(BaseModel):
    """Per-repo/audit-type budget tracking state."""

    repo_id: str
    audit_type: str
    period_start: datetime
    period_end: datetime
    max_runs: int
    runs_used: int = 0
    last_run_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def runs_remaining(self) -> int:
        return max(0, self.max_runs - self.runs_used)

    @property
    def is_exhausted(self) -> bool:
        return self.runs_used >= self.max_runs


class AuditCooldownState(BaseModel):
    """Per-repo/audit-type cooldown tracking state."""

    repo_id: str
    audit_type: str
    cooldown_seconds: float
    last_run_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_in_cooldown(self, now: datetime | None = None) -> bool:
        if self.last_run_at is None:
            return False
        t = now or datetime.now(UTC)
        elapsed = (t - self.last_run_at).total_seconds()
        return elapsed < self.cooldown_seconds

    def seconds_remaining(self, now: datetime | None = None) -> float:
        if self.last_run_at is None:
            return 0.0
        t = now or datetime.now(UTC)
        elapsed = (t - self.last_run_at).total_seconds()
        return max(0.0, self.cooldown_seconds - elapsed)


# ---------------------------------------------------------------------------
# Configuration (runtime, non-serializable)
# ---------------------------------------------------------------------------

@dataclass
class BudgetConfig:
    """Budget configuration for a repo/audit_type pair."""

    max_runs: int = 10
    period_days: int = 7


@dataclass
class CooldownConfig:
    """Cooldown configuration for a repo/audit_type pair."""

    cooldown_seconds: float = 3600.0  # 1 hour default


@dataclass
class GovernanceConfig:
    """Runtime governance configuration.

    Defines which repos/types are known, and policy thresholds.
    Does not import managed repo code or producer internals.
    """

    known_repos: list[str] = field(default_factory=list)
    known_audit_types: dict[str, list[str]] = field(default_factory=dict)
    budget_config: dict[str, dict[str, BudgetConfig]] = field(default_factory=dict)
    cooldown_config: dict[str, dict[str, CooldownConfig]] = field(default_factory=dict)
    state_dir: Path = field(default_factory=lambda: Path("tools/audit/governance/state"))
    require_mini_regression_for_urgency: list[str] = field(
        default_factory=lambda: ["low", "normal"]
    )

    def get_budget_config(self, repo_id: str, audit_type: str) -> BudgetConfig:
        return (
            self.budget_config.get(repo_id, {}).get(audit_type, BudgetConfig())
        )

    def get_cooldown_config(self, repo_id: str, audit_type: str) -> CooldownConfig:
        return (
            self.cooldown_config.get(repo_id, {}).get(audit_type, CooldownConfig())
        )


# ---------------------------------------------------------------------------
# Governed run result
# ---------------------------------------------------------------------------

class AuditGovernedRunResult(BaseModel, frozen=True):
    """Result of a governed audit attempt.

    dispatch_result is populated only when dispatch was actually called.
    A governance report is always written.
    """

    request: AuditGovernanceRequest
    decision: AuditGovernanceDecision
    governance_status: GovernanceStatus
    dispatch_result: ManagedAuditDispatchResult | None = None
    report_path: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def was_dispatched(self) -> bool:
        return self.dispatch_result is not None

    @property
    def succeeded(self) -> bool:
        return self.governance_status == "approved_and_dispatched" and (
            self.dispatch_result is not None and self.dispatch_result.succeeded
        )


# ---------------------------------------------------------------------------
# Governance report
# ---------------------------------------------------------------------------

class DispatchResultSummary(BaseModel, frozen=True):
    """Compact summary of dispatch result for governance report."""

    run_id: str | None
    status: str
    failure_kind: str | None = None
    duration_seconds: float | None = None
    artifact_manifest_path: str | None = None
    error: str | None = None


class BudgetStateSummary(BaseModel, frozen=True):
    """Snapshot of budget state at governance evaluation time."""

    runs_used: int
    max_runs: int
    runs_remaining: int
    period_start: datetime | None = None
    period_end: datetime | None = None


class CooldownStateSummary(BaseModel, frozen=True):
    """Snapshot of cooldown state at governance evaluation time."""

    in_cooldown: bool
    cooldown_seconds: float
    seconds_remaining: float
    last_run_at: datetime | None = None


class AuditGovernanceReport(BaseModel):
    """Durable governance evidence for every request.

    Written to: {output_dir}/{repo_id}/{audit_type}/{request_id}/governance_report.json
    """

    schema_version: str = "1.0"
    request: AuditGovernanceRequest
    decision: AuditGovernanceDecision
    policy_results: list[PolicyResult]
    approval: AuditManualApproval | None = None
    dispatch_result_summary: DispatchResultSummary | None = None
    budget_state_summary: BudgetStateSummary | None = None
    cooldown_state_summary: CooldownStateSummary | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def dispatched_run_id(self) -> str | None:
        """The run_id from dispatch, if the audit was dispatched."""
        if self.dispatch_result_summary is None:
            return None
        return self.dispatch_result_summary.run_id


__all__ = [
    "AuditBudgetState",
    "AuditCooldownState",
    "AuditGovernanceDecision",
    "AuditGovernanceReport",
    "AuditGovernanceRequest",
    "AuditGovernedRunResult",
    "AuditManualApproval",
    "AuditUrgency",
    "BudgetConfig",
    "BudgetStateSummary",
    "CooldownConfig",
    "CooldownStateSummary",
    "DispatchResultSummary",
    "GovernanceConfig",
    "GovernanceDecisionValue",
    "GovernanceStatus",
    "PolicyResult",
    "PolicyStatus",
    "make_request_id",
]


def make_request_id(repo_id: str, audit_type: str) -> str:
    """Generate a stable, path-safe governance request ID."""
    return _make_request_id(repo_id, audit_type)
