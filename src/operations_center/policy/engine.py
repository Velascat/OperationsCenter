# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
policy/engine.py — PolicyEngine: evaluate guardrails for planned/routed work.

The engine accepts a canonical TaskProposal + LaneDecision (+ optional
ExecutionRequest) and returns an inspectable PolicyDecision.

Evaluation logic (in order):
  1. Repo enabled check
  2. Task type allow/block list
  3. Routing constraint check (local_only label vs remote lane)
  4. Path restriction check (allowed_paths vs PathPolicy rules)
  5. Branch guardrail check
  6. Tool guardrail check (network mode, destructive actions)
  7. Validation requirement check
  8. Review requirement check
  9. Aggregate violations/warnings → determine PolicyStatus

PolicyStatus is determined as:
  BLOCK              if any blocking violation present
  REQUIRE_REVIEW     if any review violation present (no blocking violations)
  ALLOW_WITH_WARNINGS if any non-blocking violation or warning (no block/review)
  ALLOW              otherwise

The engine never collapses into backend-specific logic. It works from
canonical contract types and explicit PolicyConfig.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Optional

from operations_center.contracts.enums import LaneName
from operations_center.contracts.execution import ExecutionRequest
from operations_center.contracts.proposal import TaskProposal
from operations_center.contracts.routing import LaneDecision

from .models import (
    PathPolicy,
    PathScopeRule,
    PolicyConfig,
    PolicyDecision,
    PolicyStatus,
    PolicyViolation,
    PolicyWarning,
    RepoPolicy,
    ValidationRequirement,
)

logger = logging.getLogger(__name__)

# Lane names that are considered "local" execution.
_LOCAL_LANES = frozenset({LaneName.AIDER_LOCAL.value})

# Labels on a proposal that restrict to local execution only.
_LOCAL_ONLY_LABELS = frozenset({"local_only", "no_remote"})


class PolicyEngine:
    """Evaluates planned/routed work against guardrail policy.

    Usage::

        engine = PolicyEngine.from_defaults()
        decision = engine.evaluate(proposal, lane_decision)

        if decision.is_blocked:
            # don't proceed
        elif decision.requires_review:
            # gate on human approval
        else:
            # proceed (possibly with warnings)
    """

    def __init__(self, config: PolicyConfig) -> None:
        self._config = config

    def evaluate(
        self,
        proposal: TaskProposal,
        decision: LaneDecision,
        request: Optional[ExecutionRequest] = None,
    ) -> PolicyDecision:
        """Evaluate the proposal+decision against configured guardrails.

        Args:
            proposal: The canonical TaskProposal to evaluate.
            decision: The routing decision from SwitchBoard.
            request:  Optional ExecutionRequest for additional context
                      (allowed_paths, validation_commands, etc.)

        Returns:
            A frozen PolicyDecision with status, violations, warnings,
            and effective requirements.
        """
        repo_policy = self._config.get_repo_policy(proposal.target.repo_key)

        violations: list[PolicyViolation] = []
        warnings: list[PolicyWarning] = []

        # 1. Repo enabled
        _check_repo_enabled(repo_policy, violations)
        if any(v.blocking for v in violations):
            return _build_decision(violations, warnings, repo_policy, proposal, request)

        # 2. Task type restrictions
        _check_task_type(proposal, repo_policy, violations)

        # 3. Routing constraints from proposal labels
        _check_routing_constraints(proposal, decision, violations)

        # 4. Path restrictions
        _check_path_restrictions(proposal, repo_policy, request, violations, warnings)

        # 5. Branch guardrail
        _check_branch_guardrail(proposal, repo_policy, violations, warnings)

        # 6. Tool guardrail
        _check_tool_guardrail(proposal, decision, repo_policy, violations)

        # 7. Validation requirements
        _check_validation_requirements(proposal, repo_policy, request, violations, warnings)

        # 8. Review requirements
        _check_review_requirements(proposal, repo_policy, violations)

        return _build_decision(violations, warnings, repo_policy, proposal, request)

    @classmethod
    def from_defaults(cls) -> "PolicyEngine":
        """Create with the default conservative policy config."""
        from .defaults import DEFAULT_POLICY_CONFIG
        return cls(DEFAULT_POLICY_CONFIG)

    @classmethod
    def from_config(cls, config: PolicyConfig) -> "PolicyEngine":
        """Create with a custom policy config."""
        return cls(config)


# ---------------------------------------------------------------------------
# Evaluation sub-checks
# ---------------------------------------------------------------------------


def _check_repo_enabled(policy: RepoPolicy, violations: list[PolicyViolation]) -> None:
    if not policy.enabled:
        violations.append(PolicyViolation(
            rule_id="repo.disabled",
            category="repo",
            message=f"Repo {policy.repo_key!r} is disabled in policy configuration",
            blocking=True,
        ))


def _check_task_type(
    proposal: TaskProposal,
    policy: RepoPolicy,
    violations: list[PolicyViolation],
) -> None:
    task_type_value = proposal.task_type.value

    if policy.blocked_task_types and task_type_value in policy.blocked_task_types:
        violations.append(PolicyViolation(
            rule_id="task_type.blocked",
            category="repo",
            message=f"Task type {task_type_value!r} is blocked for repo {policy.repo_key!r}",
            blocking=True,
            related_repo=proposal.target.repo_key,
        ))

    if policy.allowed_task_types and task_type_value not in policy.allowed_task_types:
        violations.append(PolicyViolation(
            rule_id="task_type.not_in_allowlist",
            category="repo",
            message=(
                f"Task type {task_type_value!r} is not in the allowed task type list "
                f"for repo {policy.repo_key!r}"
            ),
            blocking=True,
            related_repo=proposal.target.repo_key,
        ))


def _check_routing_constraints(
    proposal: TaskProposal,
    decision: LaneDecision,
    violations: list[PolicyViolation],
) -> None:
    """Block remote lane selection when proposal labels require local execution."""
    labels = set(proposal.labels)
    if not labels.intersection(_LOCAL_ONLY_LABELS):
        return

    lane_value = decision.selected_lane.value
    if lane_value not in _LOCAL_LANES:
        triggering_labels = sorted(labels.intersection(_LOCAL_ONLY_LABELS))
        violations.append(PolicyViolation(
            rule_id="routing.local_only_violated",
            category="routing",
            message=(
                f"Proposal labels {triggering_labels} require local execution, "
                f"but routing selected remote lane {lane_value!r}"
            ),
            blocking=True,
        ))


def _check_path_restrictions(
    proposal: TaskProposal,
    policy: RepoPolicy,
    request: Optional[ExecutionRequest],
    violations: list[PolicyViolation],
    warnings: list[PolicyWarning],
) -> None:
    """Check allowed_paths against PathPolicy rules."""
    # Prefer request-level allowed_paths if present; fall back to proposal-level.
    if request is not None and request.allowed_paths:
        candidate_paths = list(request.allowed_paths)
    else:
        candidate_paths = list(proposal.target.allowed_paths)

    if not candidate_paths:
        # No explicit path restriction declared; check default_mode.
        if policy.path_policy.default_mode == "block":
            violations.append(PolicyViolation(
                rule_id="path.unrestricted_writes_blocked",
                category="path",
                message=(
                    "No path restriction declared on this task, but default path mode "
                    "is 'block'. Explicit allowed_paths must be set."
                ),
                blocking=True,
            ))
        elif policy.path_policy.default_mode == "review_required":
            warnings.append(PolicyWarning(
                rule_id="path.unrestricted_writes_review",
                category="path",
                message=(
                    "No path restriction declared on this task, but default path mode "
                    "is 'review_required'. This task needs human review."
                ),
            ))
        return

    task_type_value = proposal.task_type.value

    for path in candidate_paths:
        matched_rule = _match_path_rule(path, policy.path_policy, task_type_value)
        if matched_rule is None:
            # No rule matched; apply default mode
            if policy.path_policy.default_mode == "block":
                violations.append(PolicyViolation(
                    rule_id="path.default_blocked",
                    category="path",
                    message=f"Path {path!r} is not covered by any allow rule and default mode is 'block'",
                    blocking=True,
                    related_path=path,
                ))
            elif policy.path_policy.default_mode == "review_required":
                violations.append(PolicyViolation(
                    rule_id="path.default_review_required",
                    category="path",
                    message=f"Path {path!r} requires review (default path mode)",
                    blocking=False,
                    related_path=path,
                ))
        elif matched_rule.access_mode == "block":
            violations.append(PolicyViolation(
                rule_id="path.blocked",
                category="path",
                message=f"Path {path!r} is blocked by policy rule (pattern: {matched_rule.path_pattern!r})",
                blocking=True,
                related_path=path,
            ))
        elif matched_rule.access_mode in ("read_only", "review_required"):
            violations.append(PolicyViolation(
                rule_id="path.review_required",
                category="path",
                message=(
                    f"Path {path!r} requires human review "
                    f"(pattern: {matched_rule.path_pattern!r}; mode: {matched_rule.access_mode!r})"
                ),
                blocking=False,
                related_path=path,
            ))


def _match_path_rule(
    path: str,
    path_policy: PathPolicy,
    task_type: str,
) -> Optional[PathScopeRule]:
    """Return the first matching PathScopeRule for a path, or None."""
    for rule in path_policy.rules:
        if rule.applies_to_task_types and task_type not in rule.applies_to_task_types:
            continue
        if fnmatch.fnmatch(path, rule.path_pattern):
            return rule
    return None


def _check_branch_guardrail(
    proposal: TaskProposal,
    policy: RepoPolicy,
    violations: list[PolicyViolation],
    warnings: list[PolicyWarning],
) -> None:
    bg = policy.branch_guardrail
    branch = proposal.branch_policy

    # Check direct commit restriction
    if not bg.allow_direct_commit and not branch.branch_prefix:
        # No branch prefix means caller didn't set one; this is fine in practice —
        # we only block if the proposal explicitly sets open_pr=False AND push_on_success=False
        # (which would mean: direct commit, no PR). Emit a warning.
        warnings.append(PolicyWarning(
            rule_id="branch.no_prefix_set",
            category="branch",
            message="branch_prefix is empty; ensure a task branch is used rather than direct commit",
        ))

    # Check allowed base branches
    if bg.allowed_base_branches and proposal.target.base_branch not in bg.allowed_base_branches:
        violations.append(PolicyViolation(
            rule_id="branch.base_branch_not_allowed",
            category="branch",
            message=(
                f"Base branch {proposal.target.base_branch!r} is not in the allowed base branches "
                f"list: {bg.allowed_base_branches}"
            ),
            blocking=True,
        ))

    # Check PR requirement
    if bg.require_pr and not branch.open_pr:
        violations.append(PolicyViolation(
            rule_id="branch.pr_required",
            category="branch",
            message="Policy requires a PR, but proposal branch_policy.open_pr is False",
            blocking=False,
        ))


def _check_tool_guardrail(
    proposal: TaskProposal,
    decision: LaneDecision,
    policy: RepoPolicy,
    violations: list[PolicyViolation],
) -> None:
    tg = policy.tool_guardrail

    # Network mode: local_only means remote lanes are blocked regardless of labels
    if tg.network_mode == "local_only":
        lane_value = decision.selected_lane.value
        if lane_value not in _LOCAL_LANES:
            violations.append(PolicyViolation(
                rule_id="tool.network_local_only",
                category="tool",
                message=(
                    "Tool policy requires local-only execution, but routing "
                    f"selected remote lane {lane_value!r}"
                ),
                blocking=True,
            ))
    elif tg.network_mode == "blocked":
        violations.append(PolicyViolation(
            rule_id="tool.network_blocked",
            category="tool",
            message="All network execution is blocked by tool policy for this repo",
            blocking=True,
        ))

    # Destructive action check: if the task type or labels suggest destructive work
    destructive_indicators = {"drop_table", "rm_rf", "force_push", "destructive"}
    proposal_labels_lower = {lbl.lower() for lbl in proposal.labels}
    if not tg.allow_destructive_actions and destructive_indicators.intersection(proposal_labels_lower):
        violations.append(PolicyViolation(
            rule_id="tool.destructive_blocked",
            category="tool",
            message=(
                "Destructive actions are blocked by policy, but proposal labels "
                "indicate a potentially destructive operation"
            ),
            blocking=True,
        ))


def _check_validation_requirements(
    proposal: TaskProposal,
    policy: RepoPolicy,
    request: Optional[ExecutionRequest],
    violations: list[PolicyViolation],
    warnings: list[PolicyWarning],
) -> None:
    risk_value = proposal.risk_level.value
    task_type_value = proposal.task_type.value

    validation_available = (
        request is not None and bool(request.validation_commands)
    ) or (
        proposal.validation_profile.commands
    )

    for vr in policy.validation_requirements:
        if not _validation_req_applies(vr, risk_value, task_type_value):
            continue

        if not validation_available and vr.block_if_unavailable:
            violations.append(PolicyViolation(
                rule_id="validation.required_unavailable",
                category="validation",
                message=(
                    f"Validation profile {vr.required_profile!r} is required for "
                    f"risk={risk_value!r} but no validation commands are configured"
                ),
                blocking=True,
            ))
        elif not validation_available:
            warnings.append(PolicyWarning(
                rule_id="validation.recommended_unavailable",
                category="validation",
                message=(
                    f"Validation profile {vr.required_profile!r} is recommended for "
                    f"risk={risk_value!r} but no validation commands are configured"
                ),
            ))
        break  # Use only the first matching requirement


def _validation_req_applies(
    vr: ValidationRequirement,
    risk_level: str,
    task_type: str,
) -> bool:
    risk_match = not vr.applies_to_risk_levels or risk_level in vr.applies_to_risk_levels
    type_match = not vr.applies_to_task_types or task_type in vr.applies_to_task_types
    return risk_match and type_match


_TRUSTED_SOURCE_LABELS = frozenset({
    "source: autonomy",
    "source: spec-campaign",
    "source: board_worker",  # follow-up tasks emitted by an already-trusted run
})


def _check_review_requirements(
    proposal: TaskProposal,
    policy: RepoPolicy,
    violations: list[PolicyViolation],
) -> None:
    rr = policy.review_requirement
    risk_value = proposal.risk_level.value
    task_type_value = proposal.task_type.value
    labels = set(proposal.labels)

    if rr.blocked_without_human:
        violations.append(PolicyViolation(
            rule_id="review.blocked_without_human",
            category="review",
            message="Policy requires explicit human approval for this repo; autonomous execution is blocked",
            blocking=True,
        ))
        return

    # Tasks from pre-authorized lanes (autonomy tier, spec campaigns) bypass the
    # task-type and risk-level review gates — the operator already approved the
    # category by raising the family's autonomy tier or by registering the
    # campaign. Explicit `review_required` labels and `autonomous_allowed=False`
    # still apply, so per-task and per-repo overrides keep working.
    trusted = bool(_TRUSTED_SOURCE_LABELS.intersection(labels))

    needs_review = (
        not rr.autonomous_allowed
        or "review_required" in labels
        or (
            not trusted
            and (
                risk_value in rr.require_review_for_risk_levels
                or task_type_value in rr.require_review_for_task_types
            )
        )
    )

    if needs_review:
        violations.append(PolicyViolation(
            rule_id="review.required",
            category="review",
            message=(
                f"Human review required: risk={risk_value!r}, "
                f"task_type={task_type_value!r}"
            ),
            blocking=False,
        ))


# ---------------------------------------------------------------------------
# Decision builder
# ---------------------------------------------------------------------------


def _build_decision(
    violations: list[PolicyViolation],
    warnings: list[PolicyWarning],
    policy: RepoPolicy,
    proposal: TaskProposal,
    request: Optional[ExecutionRequest],
) -> PolicyDecision:
    """Aggregate violations/warnings into a final PolicyDecision."""
    status = _determine_status(violations, warnings)

    effective_profile = _effective_validation_profile(proposal, policy, request)
    effective_review = _effective_review_requirement(proposal, policy, violations)
    effective_scope = _effective_scope(proposal, request)

    notes = _build_notes(violations, warnings)

    return PolicyDecision(
        status=status,
        violations=violations,
        warnings=warnings,
        effective_validation_profile=effective_profile,
        effective_review_requirement=effective_review,
        effective_scope=effective_scope,
        notes=notes,
    )


def _determine_status(
    violations: list[PolicyViolation],
    warnings: list[PolicyWarning],
) -> PolicyStatus:
    if any(v.blocking for v in violations):
        return PolicyStatus.BLOCK
    if any(not v.blocking for v in violations):
        return PolicyStatus.REQUIRE_REVIEW
    if warnings:
        return PolicyStatus.ALLOW_WITH_WARNINGS
    return PolicyStatus.ALLOW


def _effective_validation_profile(
    proposal: TaskProposal,
    policy: RepoPolicy,
    request: Optional[ExecutionRequest],
) -> str:
    risk_value = proposal.risk_level.value
    task_type_value = proposal.task_type.value
    for vr in policy.validation_requirements:
        if _validation_req_applies(vr, risk_value, task_type_value):
            return vr.required_profile
    return "standard"


def _effective_review_requirement(
    proposal: TaskProposal,
    policy: RepoPolicy,
    violations: list[PolicyViolation],
) -> str:
    for v in violations:
        if v.category == "review" and not v.blocking:
            return "required"
    if not policy.review_requirement.autonomous_allowed:
        return "required"
    return "autonomous"


def _effective_scope(
    proposal: TaskProposal,
    request: Optional[ExecutionRequest],
) -> list[str]:
    if request is not None and request.allowed_paths:
        return list(request.allowed_paths)
    return list(proposal.target.allowed_paths)


def _build_notes(
    violations: list[PolicyViolation],
    warnings: list[PolicyWarning],
) -> str:
    parts: list[str] = []
    if violations:
        rule_ids = [v.rule_id for v in violations]
        parts.append(f"violations: {', '.join(rule_ids)}")
    if warnings:
        rule_ids = [w.rule_id for w in warnings]
        parts.append(f"warnings: {', '.join(rule_ids)}")
    return "; ".join(parts)
