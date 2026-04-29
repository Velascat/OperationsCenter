# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""
policy/defaults.py — conservative default guardrail policy.

The default policy applies when no repo-specific configuration is present.
It is intentionally conservative: changes go through branches, high-risk work
requires review, destructive actions are blocked, and sensitive paths trigger
review when touched.

An operator can always relax these defaults by providing explicit repo policies.
What the defaults must prevent is silent bypass of safety boundaries.

Default posture:
  - branch-based changes only (no direct commit to base)
  - high-risk tasks require human review
  - medium-risk tasks get warnings if validation is unavailable
  - critical repo config paths trigger review
  - destructive actions blocked
  - remote execution allowed unless task labels say otherwise
  - standard validation required for all risk levels
"""

from __future__ import annotations

from .models import (
    BranchGuardrail,
    PathPolicy,
    PathScopeRule,
    PolicyConfig,
    RepoPolicy,
    ReviewRequirement,
    ToolGuardrail,
    ValidationRequirement,
)


# ---------------------------------------------------------------------------
# Sensitive path patterns that trigger review by default
# ---------------------------------------------------------------------------

_SENSITIVE_PATHS = [
    PathScopeRule(
        path_pattern="*.env",
        access_mode="review_required",
        notes="Environment files may contain credentials or infra config",
    ),
    PathScopeRule(
        path_pattern=".env*",
        access_mode="review_required",
        notes="Environment files may contain credentials or infra config",
    ),
    PathScopeRule(
        path_pattern="config/secrets*",
        access_mode="review_required",
        notes="Secrets configuration",
    ),
    PathScopeRule(
        path_pattern="**/secrets/**",
        access_mode="review_required",
        notes="Secrets directory",
    ),
    PathScopeRule(
        path_pattern="docker-compose.yml",
        access_mode="review_required",
        notes="Infrastructure definition",
    ),
    PathScopeRule(
        path_pattern="docker-compose*.yml",
        access_mode="review_required",
        notes="Infrastructure definition",
    ),
    PathScopeRule(
        path_pattern=".github/workflows/**",
        access_mode="review_required",
        notes="CI/CD pipeline definitions",
    ),
    PathScopeRule(
        path_pattern="Dockerfile*",
        access_mode="review_required",
        notes="Container build definitions",
    ),
    PathScopeRule(
        path_pattern="**/migrations/**",
        access_mode="review_required",
        notes="Database migrations — irreversible changes",
    ),
]

# Paths that are always blocked for autonomous changes.
_BLOCKED_PATHS = [
    PathScopeRule(
        path_pattern="**/.ssh/**",
        access_mode="block",
        notes="SSH keys must never be written by automation",
    ),
    PathScopeRule(
        path_pattern="**/.gnupg/**",
        access_mode="block",
        notes="GPG keys must never be written by automation",
    ),
]


# ---------------------------------------------------------------------------
# Default repo policy (catch-all)
# ---------------------------------------------------------------------------

DEFAULT_REPO_POLICY = RepoPolicy(
    repo_key="*",
    enabled=True,
    risk_profile="standard",
    path_policy=PathPolicy(
        rules=_BLOCKED_PATHS + _SENSITIVE_PATHS,
        default_mode="allow",
    ),
    branch_guardrail=BranchGuardrail(
        allow_direct_commit=False,
        require_branch=True,
        branch_name_pattern="auto/*",
        require_pr=False,
        allowed_base_branches=[],
    ),
    tool_guardrail=ToolGuardrail(
        network_mode="allowed",
        blocked_tool_classes=[],
        allow_destructive_actions=False,
    ),
    validation_requirements=[
        ValidationRequirement(
            applies_to_risk_levels=["high"],
            required_profile="strict",
            must_pass=True,
            block_if_unavailable=True,
        ),
        ValidationRequirement(
            applies_to_risk_levels=["medium"],
            required_profile="standard",
            must_pass=True,
            block_if_unavailable=False,
        ),
        ValidationRequirement(
            applies_to_risk_levels=["low"],
            required_profile="standard",
            must_pass=False,
            block_if_unavailable=False,
        ),
    ],
    review_requirement=ReviewRequirement(
        autonomous_allowed=True,
        require_review_for_risk_levels=["high"],
        require_review_for_task_types=["feature", "refactor"],
        blocked_without_human=False,
    ),
    allowed_task_types=[],   # all task types allowed by default
    blocked_task_types=[],
)


# ---------------------------------------------------------------------------
# Default policy config
# ---------------------------------------------------------------------------

DEFAULT_POLICY_CONFIG = PolicyConfig(
    repo_policies=[DEFAULT_REPO_POLICY],
    default_policy=DEFAULT_REPO_POLICY,
)
