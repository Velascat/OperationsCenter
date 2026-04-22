"""
Unit tests for policy/validate.py — validate_config().

Covers: valid configs return empty list; specific invalid/contradictory
configs return error messages containing expected keywords.
"""

from __future__ import annotations

import pytest

from control_plane.policy.models import (
    BranchGuardrail,
    PathPolicy,
    PathScopeRule,
    PolicyConfig,
    RepoPolicy,
    ReviewRequirement,
    ToolGuardrail,
    ValidationRequirement,
)
from control_plane.policy.validate import validate_config
from control_plane.policy.defaults import DEFAULT_POLICY_CONFIG

from .conftest import make_policy_config, make_repo_policy


def _make_config(*policies: RepoPolicy) -> PolicyConfig:
    policies_list = list(policies)
    default = policies_list[0] if policies_list else None
    return PolicyConfig(repo_policies=policies_list, default_policy=default)


# ---------------------------------------------------------------------------
# Valid configs
# ---------------------------------------------------------------------------


class TestValidConfigs:
    def test_default_config_is_valid(self):
        assert validate_config(DEFAULT_POLICY_CONFIG) == []

    def test_minimal_config_is_valid(self):
        policy = make_repo_policy()
        errors = validate_config(make_policy_config(policy))
        assert errors == []

    def test_empty_config_is_valid(self):
        config = PolicyConfig(repo_policies=[], default_policy=None)
        errors = validate_config(config)
        assert errors == []

    def test_multiple_valid_repo_policies(self):
        p1 = make_repo_policy(repo_key="api-service")
        p2 = make_repo_policy(repo_key="auth-service")
        config = PolicyConfig(repo_policies=[p1, p2], default_policy=p1)
        assert validate_config(config) == []

    def test_all_valid_access_modes(self):
        for mode in ("allow", "read_only", "block", "review_required"):
            rule = PathScopeRule(path_pattern="x/**", access_mode=mode)
            policy = make_repo_policy(path_rules=[rule])
            errors = validate_config(make_policy_config(policy))
            assert errors == [], f"Mode {mode!r} produced errors: {errors}"

    def test_all_valid_network_modes(self):
        for mode in ("allowed", "local_only", "blocked"):
            policy = make_repo_policy(network_mode=mode)
            errors = validate_config(make_policy_config(policy))
            assert errors == [], f"Network mode {mode!r} produced errors: {errors}"

    def test_all_valid_risk_profiles(self):
        for profile in ("standard", "elevated", "critical"):
            policy = make_repo_policy(risk_profile=profile)
            errors = validate_config(make_policy_config(policy))
            assert errors == [], f"Risk profile {profile!r} produced errors: {errors}"


# ---------------------------------------------------------------------------
# Invalid configs
# ---------------------------------------------------------------------------


class TestInvalidAccessMode:
    def test_invalid_access_mode_produces_error(self):
        rule = PathScopeRule(path_pattern="src/**", access_mode="write")
        policy = make_repo_policy(path_rules=[rule])
        errors = validate_config(make_policy_config(policy))
        assert any("access_mode" in e for e in errors)
        assert any("write" in e for e in errors)


class TestInvalidNetworkMode:
    def test_invalid_network_mode_produces_error(self):
        policy = make_repo_policy(network_mode="wifi_only")
        errors = validate_config(make_policy_config(policy))
        assert any("network_mode" in e for e in errors)
        assert any("wifi_only" in e for e in errors)


class TestInvalidRiskProfile:
    def test_invalid_risk_profile_produces_error(self):
        policy = make_repo_policy(risk_profile="extreme")
        errors = validate_config(make_policy_config(policy))
        assert any("risk_profile" in e for e in errors)
        assert any("extreme" in e for e in errors)


class TestInvalidDefaultMode:
    def test_invalid_default_mode_produces_error(self):
        policy = RepoPolicy(
            repo_key="test",
            path_policy=PathPolicy(rules=[], default_mode="passthrough"),
        )
        errors = validate_config(make_policy_config(policy))
        assert any("default_mode" in e for e in errors)


class TestInvalidRiskLevelInValidationReq:
    def test_invalid_risk_level_in_validation_req(self):
        vr = ValidationRequirement(
            applies_to_risk_levels=["extreme"],
            required_profile="standard",
        )
        policy = make_repo_policy(validation_requirements=[vr])
        errors = validate_config(make_policy_config(policy))
        assert any("extreme" in e for e in errors)

    def test_invalid_risk_level_in_review_req(self):
        policy = make_repo_policy(require_review_for_risk_levels=["extreme"])
        errors = validate_config(make_policy_config(policy))
        assert any("extreme" in e for e in errors)


class TestContradictions:
    def test_allow_direct_commit_contradicts_require_branch(self):
        policy = RepoPolicy(
            repo_key="test",
            branch_guardrail=BranchGuardrail(
                allow_direct_commit=True,
                require_branch=True,
            ),
        )
        errors = validate_config(make_policy_config(policy))
        assert any("allow_direct_commit" in e and "require_branch" in e for e in errors)

    def test_blocked_without_human_contradicts_autonomous_allowed(self):
        policy = RepoPolicy(
            repo_key="test",
            review_requirement=ReviewRequirement(
                blocked_without_human=True,
                autonomous_allowed=True,
            ),
        )
        errors = validate_config(make_policy_config(policy))
        assert any("blocked_without_human" in e and "autonomous_allowed" in e for e in errors)

    def test_disabled_repo_with_allowed_task_types_produces_error(self):
        policy = make_repo_policy(
            enabled=False,
            allowed_task_types=["bug_fix"],
        )
        errors = validate_config(make_policy_config(policy))
        assert any("enabled=False" in e and "allowed_task_types" in e for e in errors)


class TestDuplicateRepoKeys:
    def test_duplicate_repo_keys_produces_error(self):
        p1 = make_repo_policy(repo_key="api-service")
        p2 = make_repo_policy(repo_key="api-service")
        config = PolicyConfig(repo_policies=[p1, p2], default_policy=p1)
        errors = validate_config(config)
        assert any("api-service" in e for e in errors)
        assert any("Duplicate" in e for e in errors)

    def test_unique_repo_keys_no_error(self):
        p1 = make_repo_policy(repo_key="api-service")
        p2 = make_repo_policy(repo_key="auth-service")
        config = PolicyConfig(repo_policies=[p1, p2], default_policy=p1)
        assert validate_config(config) == []


class TestEmptyFields:
    def test_empty_repo_key_produces_error(self):
        policy = RepoPolicy(repo_key="")
        errors = validate_config(make_policy_config(policy))
        assert any("repo_key" in e for e in errors)

    def test_empty_path_pattern_produces_error(self):
        rule = PathScopeRule(path_pattern="", access_mode="allow")
        policy = make_repo_policy(path_rules=[rule])
        errors = validate_config(make_policy_config(policy))
        assert any("path_pattern" in e for e in errors)

    def test_empty_required_profile_produces_error(self):
        vr = ValidationRequirement(
            applies_to_risk_levels=["low"],
            required_profile="",
        )
        policy = make_repo_policy(validation_requirements=[vr])
        errors = validate_config(make_policy_config(policy))
        assert any("required_profile" in e for e in errors)
