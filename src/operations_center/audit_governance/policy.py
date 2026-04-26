"""Deterministic governance policy checks.

evaluate_governance_policies() runs all checks in order.
make_governance_decision() assembles an AuditGovernanceDecision from results.

Policy checks are pure functions — no side effects, no state writes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .models import (
    AuditBudgetState,
    AuditCooldownState,
    AuditGovernanceDecision,
    AuditGovernanceRequest,
    BudgetStateSummary,
    CooldownStateSummary,
    PolicyResult,
)

# ---------------------------------------------------------------------------
# Individual policy checks
# ---------------------------------------------------------------------------

def _check_manual_request_required(request: AuditGovernanceRequest) -> PolicyResult:
    """requested_by must be a non-empty, non-system string."""
    if request.requested_by.strip():
        return PolicyResult(
            policy_name="manual_request_required",
            status="passed",
            reason="requested_by is present.",
        )
    return PolicyResult(
        policy_name="manual_request_required",
        status="failed",
        reason="requested_by is empty — full audits require an identified requester.",
    )


def _check_known_repo(request: AuditGovernanceRequest, known_repos: list[str]) -> PolicyResult:
    """repo_id must appear in the configured known_repos list."""
    if not known_repos:
        return PolicyResult(
            policy_name="known_repo_required",
            status="failed",
            reason="No known_repos configured — all repo_id values are denied until known_repos is populated.",
            details="Configure known_repos in GovernanceConfig to permit specific repos.",
        )
    if request.repo_id in known_repos:
        return PolicyResult(
            policy_name="known_repo_required",
            status="passed",
            reason=f"repo_id {request.repo_id!r} is a known managed repo.",
        )
    return PolicyResult(
        policy_name="known_repo_required",
        status="failed",
        reason=f"repo_id {request.repo_id!r} is not in the known_repos list.",
        details=f"Known repos: {', '.join(known_repos) or 'none'}",
    )


def _check_known_audit_type(
    request: AuditGovernanceRequest,
    known_audit_types: dict[str, list[str]],
) -> PolicyResult:
    """audit_type must be a configured type for the given repo_id."""
    if not known_audit_types:
        return PolicyResult(
            policy_name="known_audit_type_required",
            status="failed",
            reason="known_audit_types is empty — all audit types are denied.",
            details="Configure known_audit_types in GovernanceConfig.",
        )
    allowed = known_audit_types.get(request.repo_id)
    if allowed is None:
        return PolicyResult(
            policy_name="known_audit_type_required",
            status="warning",
            reason=f"No audit types configured for repo {request.repo_id!r}.",
            details="Configure known_audit_types in GovernanceConfig to enforce this check.",
        )
    if request.audit_type in allowed:
        return PolicyResult(
            policy_name="known_audit_type_required",
            status="passed",
            reason=f"audit_type {request.audit_type!r} is valid for {request.repo_id!r}.",
        )
    return PolicyResult(
        policy_name="known_audit_type_required",
        status="failed",
        reason=(
            f"audit_type {request.audit_type!r} is not configured for repo {request.repo_id!r}."
        ),
        details=f"Allowed types: {', '.join(allowed)}",
    )


def _check_cooldown_policy(
    request: AuditGovernanceRequest,
    cooldown_state: AuditCooldownState | None,
) -> PolicyResult:
    """Enforce minimum gap between consecutive full audits for the same repo/type."""
    if cooldown_state is None:
        return PolicyResult(
            policy_name="cooldown_policy",
            status="skipped",
            reason="No cooldown state available; check skipped.",
        )
    now = datetime.now(UTC)
    if not cooldown_state.is_in_cooldown(now):
        last = (
            f"Last run: {cooldown_state.last_run_at.isoformat()}"
            if cooldown_state.last_run_at
            else "No previous runs."
        )
        return PolicyResult(
            policy_name="cooldown_policy",
            status="passed",
            reason="Cooldown period has elapsed.",
            details=last,
        )
    remaining = cooldown_state.seconds_remaining(now)
    return PolicyResult(
        policy_name="cooldown_policy",
        status="failed",
        reason=(
            f"Cooldown active: {remaining:.0f}s remaining "
            f"(cooldown={cooldown_state.cooldown_seconds:.0f}s)."
        ),
        details=(
            f"Last run: {cooldown_state.last_run_at.isoformat() if cooldown_state.last_run_at else 'unknown'}"
        ),
    )


def _check_budget_policy(
    request: AuditGovernanceRequest,
    budget_state: AuditBudgetState | None,
) -> PolicyResult:
    """Enforce per-period run limit for the repo/audit_type."""
    if budget_state is None:
        return PolicyResult(
            policy_name="budget_policy",
            status="skipped",
            reason="No budget state available; check skipped.",
        )
    if not budget_state.is_exhausted:
        return PolicyResult(
            policy_name="budget_policy",
            status="passed",
            reason=(
                f"Budget available: {budget_state.runs_remaining} of "
                f"{budget_state.max_runs} runs remaining in period."
            ),
        )
    return PolicyResult(
        policy_name="budget_policy",
        status="failed",
        reason=(
            f"Budget exhausted: {budget_state.runs_used}/{budget_state.max_runs} runs "
            f"used in period ending {budget_state.period_end.isoformat()}."
        ),
    )


def _check_mini_regression_first(
    request: AuditGovernanceRequest,
    require_for_urgency: list[str],
) -> PolicyResult:
    """Prefer Phase 11 mini regression before requesting full audits.

    For low/normal urgency with no related suite report: needs_manual_approval.
    For high/urgent with no related suite report: still a warning (not hard fail).
    """
    has_evidence = bool(request.related_suite_report_path)
    urgency_requires = request.urgency in require_for_urgency

    if has_evidence:
        return PolicyResult(
            policy_name="mini_regression_first_policy",
            status="passed",
            reason="Related mini regression suite report is present.",
            details=f"Suite report: {request.related_suite_report_path}",
        )
    if urgency_requires:
        return PolicyResult(
            policy_name="mini_regression_first_policy",
            status="failed",
            reason=(
                f"No related mini regression suite report found for urgency={request.urgency!r}. "
                "Run Phase 11 mini regression first or provide related_suite_report_path."
            ),
        )
    # high/urgent without evidence — warn but do not hard-deny
    return PolicyResult(
        policy_name="mini_regression_first_policy",
        status="warning",
        reason=(
            f"No related mini regression suite report for urgency={request.urgency!r}. "
            "Consider running Phase 11 first."
        ),
    )


def _check_urgent_override(request: AuditGovernanceRequest) -> PolicyResult:
    """High/urgent requests must use manual approval — they do not auto-approve."""
    if request.urgency in ("high", "urgent"):
        return PolicyResult(
            policy_name="urgent_override_policy",
            status="warning",
            reason=(
                f"urgency={request.urgency!r} requires manual approval before dispatch. "
                "Urgent requests are never auto-approved."
            ),
        )
    return PolicyResult(
        policy_name="urgent_override_policy",
        status="passed",
        reason=f"urgency={request.urgency!r} does not require urgent override handling.",
    )


def _check_recent_success(
    request: AuditGovernanceRequest,
    cooldown_state: AuditCooldownState | None,
) -> PolicyResult:
    """If allow_if_recent_success and last run succeeded recently, defer instead of deny."""
    if not request.allow_if_recent_success:
        return PolicyResult(
            policy_name="recent_success_policy",
            status="skipped",
            reason="allow_if_recent_success is False; check skipped.",
        )
    has_recent = (
        cooldown_state is not None
        and cooldown_state.last_run_at is not None
        and cooldown_state.is_in_cooldown()
    )
    if has_recent:
        return PolicyResult(
            policy_name="recent_success_policy",
            status="warning",
            reason="A recent run exists within the cooldown window; consider deferring.",
            details=(
                f"Last run: {cooldown_state.last_run_at.isoformat() if cooldown_state and cooldown_state.last_run_at else 'unknown'}"
            ),
        )
    return PolicyResult(
        policy_name="recent_success_policy",
        status="passed",
        reason="No recent run within cooldown window; proceeding normally.",
    )


# ---------------------------------------------------------------------------
# Aggregate evaluation
# ---------------------------------------------------------------------------

def evaluate_governance_policies(
    request: AuditGovernanceRequest,
    *,
    known_repos: list[str],
    known_audit_types: dict[str, list[str]],
    budget_state: AuditBudgetState | None = None,
    cooldown_state: AuditCooldownState | None = None,
    require_mini_regression_for_urgency: list[str] | None = None,
) -> list[PolicyResult]:
    """Run all policy checks and return ordered results."""
    if require_mini_regression_for_urgency is None:
        require_mini_regression_for_urgency = ["low", "normal"]

    return [
        _check_manual_request_required(request),
        _check_known_repo(request, known_repos),
        _check_known_audit_type(request, known_audit_types),
        _check_cooldown_policy(request, cooldown_state),
        _check_budget_policy(request, budget_state),
        _check_mini_regression_first(request, require_mini_regression_for_urgency),
        _check_urgent_override(request),
        _check_recent_success(request, cooldown_state),
    ]


def make_governance_decision(
    request: AuditGovernanceRequest,
    policy_results: list[PolicyResult],
) -> AuditGovernanceDecision:
    """Assemble an AuditGovernanceDecision from policy results.

    Decision priority:
    1. denied  → any hard check failed (known_repo, known_audit_type, manual_request_required)
    2. needs_manual_approval → high/urgent urgency OR mini_regression_first failed
    3. deferred  → budget/cooldown failed with low/normal urgency
    4. approved  → all required checks passed
    """
    by_name = {p.policy_name: p for p in policy_results}
    reasons: list[str] = []

    # --- Hard denials ---
    hard_deny_checks = [
        "known_repo_required",
        "known_audit_type_required",
        "manual_request_required",
    ]
    hard_failures = [
        p for p in policy_results
        if p.policy_name in hard_deny_checks and p.status == "failed"
    ]
    if hard_failures:
        for p in hard_failures:
            reasons.append(p.reason)
        return AuditGovernanceDecision(
            request_id=request.request_id,
            repo_id=request.repo_id,
            audit_type=request.audit_type,
            decision="denied",
            reasons=reasons,
            policy_results=policy_results,
            requires_manual_approval=False,
        )

    # --- Needs manual approval (urgent override or missing evidence) ---
    urgent_result = by_name.get("urgent_override_policy")
    mini_result = by_name.get("mini_regression_first_policy")

    needs_manual = (
        (urgent_result and urgent_result.status == "warning")
        or (mini_result and mini_result.status == "failed")
    )

    # Budget/cooldown failures with high/urgent urgency also escalate to manual
    budget_failed = (
        by_name.get("budget_policy", PolicyResult(
            policy_name="budget_policy", status="skipped", reason=""
        )).status == "failed"
    )
    cooldown_failed = (
        by_name.get("cooldown_policy", PolicyResult(
            policy_name="cooldown_policy", status="skipped", reason=""
        )).status == "failed"
    )

    if needs_manual or (request.urgency in ("high", "urgent") and (budget_failed or cooldown_failed)):
        for p in policy_results:
            if p.status in ("failed", "warning"):
                reasons.append(p.reason)
        if not reasons:
            reasons.append("Manual approval required based on urgency or evidence rules.")
        return AuditGovernanceDecision(
            request_id=request.request_id,
            repo_id=request.repo_id,
            audit_type=request.audit_type,
            decision="needs_manual_approval",
            reasons=reasons,
            policy_results=policy_results,
            requires_manual_approval=True,
        )

    # --- Deferred (budget/cooldown failure with low/normal urgency) ---
    if budget_failed or cooldown_failed:
        for p in policy_results:
            if p.status == "failed":
                reasons.append(p.reason)
        return AuditGovernanceDecision(
            request_id=request.request_id,
            repo_id=request.repo_id,
            audit_type=request.audit_type,
            decision="deferred",
            reasons=reasons,
            policy_results=policy_results,
            requires_manual_approval=False,
        )

    # --- Approved ---
    reasons.append("All required policy checks passed.")
    return AuditGovernanceDecision(
        request_id=request.request_id,
        repo_id=request.repo_id,
        audit_type=request.audit_type,
        decision="approved",
        reasons=reasons,
        policy_results=policy_results,
        requires_manual_approval=False,
    )


__all__ = [
    "evaluate_governance_policies",
    "make_governance_decision",
]
