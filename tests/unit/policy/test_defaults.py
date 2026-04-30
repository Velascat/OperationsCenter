# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
Unit tests for policy/defaults.py.

Verifies that DEFAULT_REPO_POLICY and DEFAULT_POLICY_CONFIG are:
  - valid (no contradictions)
  - conservatively configured (branch-required, high-risk gated, etc.)
  - match documented posture in the module docstring
"""

from __future__ import annotations


from operations_center.policy.defaults import DEFAULT_POLICY_CONFIG, DEFAULT_REPO_POLICY
from operations_center.policy.validate import validate_config


class TestDefaultRepoPolicyStructure:
    def test_is_enabled(self):
        assert DEFAULT_REPO_POLICY.enabled is True

    def test_catches_all_repos(self):
        assert DEFAULT_REPO_POLICY.repo_key == "*"

    def test_standard_risk_profile(self):
        assert DEFAULT_REPO_POLICY.risk_profile == "standard"

    def test_no_direct_commit(self):
        assert DEFAULT_REPO_POLICY.branch_guardrail.allow_direct_commit is False

    def test_requires_branch(self):
        assert DEFAULT_REPO_POLICY.branch_guardrail.require_branch is True

    def test_branch_name_pattern(self):
        assert DEFAULT_REPO_POLICY.branch_guardrail.branch_name_pattern == "auto/*"

    def test_no_pr_required_by_default(self):
        assert DEFAULT_REPO_POLICY.branch_guardrail.require_pr is False

    def test_network_allowed(self):
        assert DEFAULT_REPO_POLICY.tool_guardrail.network_mode == "allowed"

    def test_destructive_actions_blocked(self):
        assert DEFAULT_REPO_POLICY.tool_guardrail.allow_destructive_actions is False

    def test_autonomous_allowed(self):
        assert DEFAULT_REPO_POLICY.review_requirement.autonomous_allowed is True

    def test_high_risk_requires_review(self):
        assert "high" in DEFAULT_REPO_POLICY.review_requirement.require_review_for_risk_levels

    def test_feature_requires_review(self):
        assert "feature" in DEFAULT_REPO_POLICY.review_requirement.require_review_for_task_types

    def test_refactor_requires_review(self):
        assert "refactor" in DEFAULT_REPO_POLICY.review_requirement.require_review_for_task_types

    def test_not_blocked_without_human(self):
        assert DEFAULT_REPO_POLICY.review_requirement.blocked_without_human is False

    def test_all_task_types_allowed_by_default(self):
        assert DEFAULT_REPO_POLICY.allowed_task_types == []
        assert DEFAULT_REPO_POLICY.blocked_task_types == []


class TestDefaultPathPolicySensitivePaths:
    def _path_rules(self):
        return DEFAULT_REPO_POLICY.path_policy.rules

    def _patterns(self):
        return [r.path_pattern for r in self._path_rules()]

    def test_env_files_review_required(self):
        patterns = self._patterns()
        assert "*.env" in patterns or ".env*" in patterns

    def test_ssh_keys_blocked(self):
        blocked = [r for r in self._path_rules() if r.access_mode == "block"]
        ssh_pattern = any(".ssh" in r.path_pattern for r in blocked)
        assert ssh_pattern

    def test_gnupg_blocked(self):
        blocked = [r for r in self._path_rules() if r.access_mode == "block"]
        gpg_pattern = any(".gnupg" in r.path_pattern for r in blocked)
        assert gpg_pattern

    def test_migrations_review_required(self):
        review = [r for r in self._path_rules() if r.access_mode == "review_required"]
        migration_pattern = any("migrations" in r.path_pattern for r in review)
        assert migration_pattern

    def test_workflows_review_required(self):
        review = [r for r in self._path_rules() if r.access_mode == "review_required"]
        workflow_pattern = any("workflows" in r.path_pattern for r in review)
        assert workflow_pattern

    def test_dockerfile_review_required(self):
        review = [r for r in self._path_rules() if r.access_mode == "review_required"]
        docker_pattern = any("Dockerfile" in r.path_pattern for r in review)
        assert docker_pattern

    def test_default_mode_is_allow(self):
        assert DEFAULT_REPO_POLICY.path_policy.default_mode == "allow"

    def test_has_at_least_two_blocked_paths(self):
        blocked = [r for r in self._path_rules() if r.access_mode == "block"]
        assert len(blocked) >= 2

    def test_has_several_review_required_paths(self):
        review = [r for r in self._path_rules() if r.access_mode == "review_required"]
        assert len(review) >= 4


class TestDefaultValidationRequirements:
    def _reqs(self):
        return DEFAULT_REPO_POLICY.validation_requirements

    def test_has_at_least_three_requirements(self):
        assert len(self._reqs()) >= 3

    def test_high_risk_blocks_if_unavailable(self):
        high_reqs = [r for r in self._reqs() if "high" in r.applies_to_risk_levels]
        assert high_reqs
        assert all(r.block_if_unavailable for r in high_reqs)

    def test_high_risk_requires_strict_profile(self):
        high_reqs = [r for r in self._reqs() if "high" in r.applies_to_risk_levels]
        assert all(r.required_profile == "strict" for r in high_reqs)

    def test_medium_risk_requires_standard_profile(self):
        med_reqs = [r for r in self._reqs() if "medium" in r.applies_to_risk_levels]
        assert med_reqs
        assert all(r.required_profile == "standard" for r in med_reqs)

    def test_medium_risk_does_not_block_if_unavailable(self):
        med_reqs = [r for r in self._reqs() if "medium" in r.applies_to_risk_levels]
        assert not all(r.block_if_unavailable for r in med_reqs)


class TestDefaultPolicyConfig:
    def test_has_default_policy(self):
        assert DEFAULT_POLICY_CONFIG.default_policy is not None

    def test_has_repo_policies(self):
        assert len(DEFAULT_POLICY_CONFIG.repo_policies) >= 1

    def test_default_policy_is_conservative(self):
        config = DEFAULT_POLICY_CONFIG
        errors = validate_config(config)
        assert errors == [], f"Default config has validation errors: {errors}"

    def test_get_repo_policy_returns_wildcard_for_unknown_repo(self):
        result = DEFAULT_POLICY_CONFIG.get_repo_policy("some-unknown-repo")
        assert result is not None
