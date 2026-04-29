# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""
policy/validate.py — validate PolicyConfig for internal consistency.

validate_config() returns a list of human-readable error strings.
An empty list means the config is valid.

This is a defensive check, not a schema validator. It catches logical
contradictions and obviously dangerous configurations that would silently
misbehave at evaluation time.
"""

from __future__ import annotations

from .models import PathPolicy, PolicyConfig, RepoPolicy, ToolGuardrail


_VALID_ACCESS_MODES = frozenset({"allow", "read_only", "block", "review_required"})
_VALID_NETWORK_MODES = frozenset({"allowed", "local_only", "blocked"})
_VALID_DEFAULT_MODES = frozenset({"allow", "block", "review_required"})
_VALID_RISK_LEVELS = frozenset({"low", "medium", "high"})
_VALID_RISK_PROFILES = frozenset({"standard", "elevated", "critical"})


def validate_config(config: PolicyConfig) -> list[str]:
    """Return a list of error messages for a PolicyConfig.

    Empty return = valid config.
    """
    errors: list[str] = []

    if config.default_policy is not None:
        errors.extend(_validate_repo_policy(config.default_policy, "default_policy"))

    for i, policy in enumerate(config.repo_policies):
        ctx = f"repo_policies[{i}] (repo_key={policy.repo_key!r})"
        errors.extend(_validate_repo_policy(policy, ctx))

    errors.extend(_check_duplicate_repo_keys(config))

    return errors


def _validate_repo_policy(policy: RepoPolicy, ctx: str) -> list[str]:
    errors: list[str] = []

    if not policy.repo_key:
        errors.append(f"{ctx}: repo_key must not be empty")

    if policy.risk_profile not in _VALID_RISK_PROFILES:
        errors.append(
            f"{ctx}: invalid risk_profile {policy.risk_profile!r}; "
            f"must be one of {sorted(_VALID_RISK_PROFILES)}"
        )

    errors.extend(_validate_path_policy(policy.path_policy, ctx))
    errors.extend(_validate_tool_guardrail(policy.tool_guardrail, ctx))
    errors.extend(_validate_validation_requirements(policy, ctx))
    errors.extend(_validate_review_requirement(policy, ctx))
    errors.extend(_validate_branch_guardrail(policy, ctx))
    errors.extend(_check_contradictions(policy, ctx))

    return errors


def _validate_path_policy(path_policy: PathPolicy, ctx: str) -> list[str]:
    errors: list[str] = []

    if path_policy.default_mode not in _VALID_DEFAULT_MODES:
        errors.append(
            f"{ctx}.path_policy: invalid default_mode {path_policy.default_mode!r}; "
            f"must be one of {sorted(_VALID_DEFAULT_MODES)}"
        )

    for j, rule in enumerate(path_policy.rules):
        rule_ctx = f"{ctx}.path_policy.rules[{j}] (pattern={rule.path_pattern!r})"
        if not rule.path_pattern:
            errors.append(f"{rule_ctx}: path_pattern must not be empty")
        if rule.access_mode not in _VALID_ACCESS_MODES:
            errors.append(
                f"{rule_ctx}: invalid access_mode {rule.access_mode!r}; "
                f"must be one of {sorted(_VALID_ACCESS_MODES)}"
            )

    return errors


def _validate_tool_guardrail(tool: ToolGuardrail, ctx: str) -> list[str]:
    errors: list[str] = []
    if tool.network_mode not in _VALID_NETWORK_MODES:
        errors.append(
            f"{ctx}.tool_guardrail: invalid network_mode {tool.network_mode!r}; "
            f"must be one of {sorted(_VALID_NETWORK_MODES)}"
        )
    return errors


def _validate_validation_requirements(policy: RepoPolicy, ctx: str) -> list[str]:
    errors: list[str] = []
    for i, vr in enumerate(policy.validation_requirements):
        vr_ctx = f"{ctx}.validation_requirements[{i}]"
        for rl in vr.applies_to_risk_levels:
            if rl not in _VALID_RISK_LEVELS:
                errors.append(
                    f"{vr_ctx}: invalid risk level {rl!r}; "
                    f"must be one of {sorted(_VALID_RISK_LEVELS)}"
                )
        if not vr.required_profile:
            errors.append(f"{vr_ctx}: required_profile must not be empty")
    return errors


def _validate_review_requirement(policy: RepoPolicy, ctx: str) -> list[str]:
    errors: list[str] = []
    rr = policy.review_requirement
    for rl in rr.require_review_for_risk_levels:
        if rl not in _VALID_RISK_LEVELS:
            errors.append(
                f"{ctx}.review_requirement: invalid risk level {rl!r}; "
                f"must be one of {sorted(_VALID_RISK_LEVELS)}"
            )
    return errors


def _validate_branch_guardrail(policy: RepoPolicy, ctx: str) -> list[str]:
    errors: list[str] = []
    bg = policy.branch_guardrail
    if bg.allow_direct_commit and bg.require_branch:
        errors.append(
            f"{ctx}.branch_guardrail: allow_direct_commit=True contradicts require_branch=True"
        )
    return errors


def _check_contradictions(policy: RepoPolicy, ctx: str) -> list[str]:
    errors: list[str] = []
    rr = policy.review_requirement

    if not rr.autonomous_allowed and not rr.blocked_without_human:
        # autonomous not allowed but also not blocked → require_review, which is fine
        pass

    if rr.blocked_without_human and rr.autonomous_allowed:
        errors.append(
            f"{ctx}.review_requirement: blocked_without_human=True contradicts "
            f"autonomous_allowed=True"
        )

    if not policy.enabled and policy.allowed_task_types:
        errors.append(
            f"{ctx}: enabled=False but allowed_task_types is set — "
            f"allowed_task_types has no effect when repo is disabled"
        )

    return errors


def _check_duplicate_repo_keys(config: PolicyConfig) -> list[str]:
    seen: set[str] = set()
    errors: list[str] = []
    for policy in config.repo_policies:
        if policy.repo_key in seen:
            errors.append(
                f"Duplicate repo_key {policy.repo_key!r} in repo_policies — "
                f"only the first match is used"
            )
        seen.add(policy.repo_key)
    return errors
