# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""
policy/models.py — typed policy and guardrail models.

Two model families live here:

1. Policy configuration models (@dataclass) — mutable, used to express
   guardrails for repos, paths, branches, tools, validation, and review.

2. Policy decision/output models (frozen Pydantic) — immutable, produced
   by the engine and retained for inspection.

Policy config models are separate from canonical contracts. They constrain
the contracts but do not replace them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Policy status
# ---------------------------------------------------------------------------

class PolicyStatus(str, Enum):
    """Outcome of a policy evaluation."""
    ALLOW = "allow"
    ALLOW_WITH_WARNINGS = "allow_with_warnings"
    REQUIRE_REVIEW = "require_review"
    BLOCK = "block"


# ---------------------------------------------------------------------------
# Policy configuration models (dataclasses — mutable, configuration-facing)
# ---------------------------------------------------------------------------

@dataclass
class PathScopeRule:
    """One path-based access rule.

    path_pattern uses fnmatch-style glob patterns.
    access_mode: "allow" | "read_only" | "block" | "review_required"
    applies_to_task_types: empty list = applies to all task types.
    """
    path_pattern: str
    access_mode: str  # "allow" | "read_only" | "block" | "review_required"
    notes: str = ""
    applies_to_task_types: list[str] = field(default_factory=list)


@dataclass
class PathPolicy:
    """Path-level access restrictions.

    rules are evaluated in order; first match wins.
    default_mode applies when no rule matches.
    """
    rules: list[PathScopeRule] = field(default_factory=list)
    default_mode: str = "allow"   # "allow" | "block" | "review_required"


@dataclass
class BranchGuardrail:
    """Branch and PR behavior requirements.

    allow_direct_commit: whether commits directly to base branch are permitted.
    require_branch: whether a separate task branch is required.
    branch_name_pattern: fnmatch pattern for valid task branch names.
    require_pr: whether a PR is required before merge.
    allowed_base_branches: if non-empty, only these branches may be used as
        the base. Empty = any branch is allowed.
    """
    allow_direct_commit: bool = False
    require_branch: bool = True
    branch_name_pattern: str = "auto/*"
    require_pr: bool = False
    allowed_base_branches: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class ToolGuardrail:
    """Tool and execution environment restrictions.

    network_mode: "allowed" | "local_only" | "blocked"
    blocked_tool_classes: tool category names that may not be used.
    allow_destructive_actions: if False, operations like force-push, rm -rf,
        drop-table, etc. are blocked.
    """
    network_mode: str = "allowed"
    blocked_tool_classes: list[str] = field(default_factory=list)
    allow_destructive_actions: bool = False
    notes: str = ""


@dataclass
class ValidationRequirement:
    """Validation requirement for a specific risk/task combination.

    applies_to_risk_levels: empty = applies to all risk levels.
    applies_to_task_types: empty = applies to all task types.
    required_profile: logical name of the required validation profile.
    must_pass: if True, validation failure → block execution.
    allow_partial: if True, partial validation is acceptable.
    block_if_unavailable: if True and no validation commands are present,
        the run is blocked.
    """
    applies_to_risk_levels: list[str] = field(default_factory=list)
    applies_to_task_types: list[str] = field(default_factory=list)
    required_profile: str = "standard"
    must_pass: bool = True
    allow_partial: bool = False
    block_if_unavailable: bool = False


@dataclass
class ReviewRequirement:
    """Human-review gating rules.

    autonomous_allowed: if False, all tasks require human review.
    require_review_for_risk_levels: risk levels that trigger mandatory review.
    require_review_for_task_types: task types that trigger mandatory review.
    blocked_without_human: if True, the task may not proceed at all without
        explicit human approval (even review is insufficient).
    """
    autonomous_allowed: bool = True
    require_review_for_risk_levels: list[str] = field(default_factory=list)
    require_review_for_task_types: list[str] = field(default_factory=list)
    blocked_without_human: bool = False
    notes: str = ""


@dataclass
class RepoPolicy:
    """Complete guardrail policy for one repository.

    repo_key: the exact repo key this policy applies to, or "*" for a
        catch-all default.
    enabled: if False, all work for this repo is blocked.
    risk_profile: "standard" | "elevated" | "critical" — shapes defaults.
    allowed_task_types: if non-empty, only these task types are permitted.
    blocked_task_types: task types that are never permitted.
    """
    repo_key: str
    enabled: bool = True
    risk_profile: str = "standard"
    path_policy: PathPolicy = field(default_factory=PathPolicy)
    branch_guardrail: BranchGuardrail = field(default_factory=BranchGuardrail)
    tool_guardrail: ToolGuardrail = field(default_factory=ToolGuardrail)
    validation_requirements: list[ValidationRequirement] = field(default_factory=list)
    review_requirement: ReviewRequirement = field(default_factory=ReviewRequirement)
    allowed_task_types: list[str] = field(default_factory=list)
    blocked_task_types: list[str] = field(default_factory=list)


@dataclass
class PolicyConfig:
    """Collection of repo-level policies and a default catch-all.

    repo_policies is a list ordered by specificity. get_repo_policy() finds
    the most specific match: exact repo_key first, then "*" wildcard, then
    the built-in default.
    """
    repo_policies: list[RepoPolicy] = field(default_factory=list)
    default_policy: Optional[RepoPolicy] = None

    def get_repo_policy(self, repo_key: str) -> RepoPolicy:
        """Return the most specific policy for the given repo_key."""
        for p in self.repo_policies:
            if p.repo_key == repo_key:
                return p
        for p in self.repo_policies:
            if p.repo_key == "*":
                return p
        if self.default_policy is not None:
            return self.default_policy
        from .defaults import DEFAULT_REPO_POLICY
        return DEFAULT_REPO_POLICY


# ---------------------------------------------------------------------------
# Policy decision / output models (frozen Pydantic — inspectable outputs)
# ---------------------------------------------------------------------------

class PolicyViolation(BaseModel):
    """A specific rule that was violated.

    blocking=True violations cause PolicyStatus.BLOCK.
    blocking=False violations contribute to ALLOW_WITH_WARNINGS or REQUIRE_REVIEW.
    """
    rule_id: str
    category: str = Field(
        description="Domain: 'path' | 'repo' | 'branch' | 'tool' | 'validation' | 'review' | 'routing'"
    )
    message: str
    blocking: bool = True
    related_path: Optional[str] = None
    related_repo: Optional[str] = None

    model_config = {"frozen": True}


class PolicyWarning(BaseModel):
    """A non-blocking concern flagged during policy evaluation."""
    rule_id: str
    category: str
    message: str
    related_path: Optional[str] = None

    model_config = {"frozen": True}


class PolicyDecision(BaseModel):
    """The result of evaluating a proposal/decision against guardrails.

    status summarizes the outcome. violations and warnings detail the reasons.
    effective_* fields carry the resolved requirements for this run.
    """
    decision_id: str = Field(default_factory=_new_id)
    status: PolicyStatus
    violations: list[PolicyViolation] = Field(default_factory=list)
    warnings: list[PolicyWarning] = Field(default_factory=list)
    effective_validation_profile: str = "standard"
    effective_review_requirement: str = "autonomous"
    effective_scope: list[str] = Field(
        default_factory=list,
        description="Effective allowed paths after policy application",
    )
    notes: str = ""
    evaluated_at: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": True}

    @property
    def is_allowed(self) -> bool:
        return self.status in (PolicyStatus.ALLOW, PolicyStatus.ALLOW_WITH_WARNINGS)

    @property
    def is_blocked(self) -> bool:
        return self.status == PolicyStatus.BLOCK

    @property
    def requires_review(self) -> bool:
        return self.status == PolicyStatus.REQUIRE_REVIEW


class PolicyExplanation(BaseModel):
    """Human-readable explanation of a PolicyDecision.

    Generated by explain() from a PolicyDecision and its RepoPolicy.
    """
    summary: str
    key_rules_applied: list[str] = Field(default_factory=list)
    review_reasoning: str = ""
    validation_reasoning: str = ""
    scope_reasoning: str = ""
    routing_reasoning: str = ""

    model_config = {"frozen": True}
