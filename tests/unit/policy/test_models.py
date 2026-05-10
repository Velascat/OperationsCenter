# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
Unit tests for policy/models.py.

Covers: model construction, defaults, frozen/mutable boundaries,
PolicyDecision helper properties, and PolicyStatus values.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from operations_center.policy.models import (
    BranchGuardrail,
    PathPolicy,
    PathScopeRule,
    PolicyConfig,
    PolicyDecision,
    PolicyExplanation,
    PolicyStatus,
    PolicyViolation,
    PolicyWarning,
    RepoPolicy,
    ReviewRequirement,
    ToolGuardrail,
    ValidationRequirement,
)


# ---------------------------------------------------------------------------
# PolicyStatus
# ---------------------------------------------------------------------------


class TestPolicyStatus:
    def test_status_values_are_strings(self):
        assert PolicyStatus.ALLOW == "allow"
        assert PolicyStatus.ALLOW_WITH_WARNINGS == "allow_with_warnings"
        assert PolicyStatus.REQUIRE_REVIEW == "require_review"
        assert PolicyStatus.BLOCK == "block"

    def test_all_four_statuses_exist(self):
        statuses = {s.value for s in PolicyStatus}
        assert statuses == {"allow", "allow_with_warnings", "require_review", "block"}


# ---------------------------------------------------------------------------
# PathScopeRule
# ---------------------------------------------------------------------------


class TestPathScopeRule:
    def test_minimal_construction(self):
        rule = PathScopeRule(path_pattern="src/**", access_mode="allow")
        assert rule.path_pattern == "src/**"
        assert rule.access_mode == "allow"
        assert rule.notes == ""
        assert rule.applies_to_task_types == []

    def test_full_construction(self):
        rule = PathScopeRule(
            path_pattern="*.env",
            access_mode="review_required",
            notes="credentials",
            applies_to_task_types=["feature"],
        )
        assert rule.notes == "credentials"
        assert rule.applies_to_task_types == ["feature"]

    def test_is_mutable(self):
        rule = PathScopeRule(path_pattern="x", access_mode="allow")
        rule.notes = "updated"
        assert rule.notes == "updated"


# ---------------------------------------------------------------------------
# PathPolicy
# ---------------------------------------------------------------------------


class TestPathPolicy:
    def test_defaults(self):
        pp = PathPolicy()
        assert pp.rules == []
        assert pp.default_mode == "allow"

    def test_with_rules(self):
        rule = PathScopeRule(path_pattern="*.env", access_mode="block")
        pp = PathPolicy(rules=[rule], default_mode="block")
        assert len(pp.rules) == 1
        assert pp.default_mode == "block"


# ---------------------------------------------------------------------------
# BranchGuardrail
# ---------------------------------------------------------------------------


class TestBranchGuardrail:
    def test_defaults(self):
        bg = BranchGuardrail()
        assert bg.allow_direct_commit is False
        assert bg.require_branch is True
        assert bg.branch_name_pattern == "auto/*"
        assert bg.require_pr is False
        assert bg.allowed_base_branches == []

    def test_custom_values(self):
        bg = BranchGuardrail(
            allow_direct_commit=True,
            require_branch=False,
            require_pr=True,
            allowed_base_branches=["main", "develop"],
        )
        assert bg.allow_direct_commit is True
        assert bg.allowed_base_branches == ["main", "develop"]


# ---------------------------------------------------------------------------
# ToolGuardrail
# ---------------------------------------------------------------------------


class TestToolGuardrail:
    def test_defaults(self):
        tg = ToolGuardrail()
        assert tg.network_mode == "allowed"
        assert tg.blocked_tool_classes == []
        assert tg.allow_destructive_actions is False

    def test_local_only_mode(self):
        tg = ToolGuardrail(network_mode="local_only")
        assert tg.network_mode == "local_only"


# ---------------------------------------------------------------------------
# ValidationRequirement
# ---------------------------------------------------------------------------


class TestValidationRequirement:
    def test_defaults(self):
        vr = ValidationRequirement()
        assert vr.applies_to_risk_levels == []
        assert vr.required_profile == "standard"
        assert vr.must_pass is True
        assert vr.block_if_unavailable is False

    def test_blocking_requirement(self):
        vr = ValidationRequirement(
            applies_to_risk_levels=["high"],
            required_profile="strict",
            must_pass=True,
            block_if_unavailable=True,
        )
        assert vr.block_if_unavailable is True
        assert vr.required_profile == "strict"


# ---------------------------------------------------------------------------
# ReviewRequirement
# ---------------------------------------------------------------------------


class TestReviewRequirement:
    def test_defaults(self):
        rr = ReviewRequirement()
        assert rr.autonomous_allowed is True
        assert rr.require_review_for_risk_levels == []
        assert rr.require_review_for_task_types == []
        assert rr.blocked_without_human is False

    def test_blocking_human_required(self):
        rr = ReviewRequirement(blocked_without_human=True, autonomous_allowed=False)
        assert rr.blocked_without_human is True


# ---------------------------------------------------------------------------
# RepoPolicy
# ---------------------------------------------------------------------------


class TestRepoPolicy:
    def test_minimal_construction(self):
        policy = RepoPolicy(repo_key="my-repo")
        assert policy.repo_key == "my-repo"
        assert policy.enabled is True
        assert policy.risk_profile == "standard"
        assert policy.allowed_task_types == []
        assert policy.blocked_task_types == []

    def test_is_mutable(self):
        policy = RepoPolicy(repo_key="my-repo")
        policy.enabled = False
        assert policy.enabled is False


# ---------------------------------------------------------------------------
# PolicyConfig.get_repo_policy
# ---------------------------------------------------------------------------


class TestPolicyConfigGetRepoPolicy:
    def _make_policy(self, repo_key: str) -> RepoPolicy:
        return RepoPolicy(repo_key=repo_key)

    def test_returns_exact_match(self):
        p = self._make_policy("api-service")
        config = PolicyConfig(repo_policies=[p], default_policy=self._make_policy("*"))
        result = config.get_repo_policy("api-service")
        assert result.repo_key == "api-service"

    def test_returns_wildcard_when_no_exact_match(self):
        default = self._make_policy("*")
        config = PolicyConfig(repo_policies=[], default_policy=default)
        result = config.get_repo_policy("anything")
        assert result.repo_key == "*"

    def test_exact_match_beats_wildcard(self):
        specific = self._make_policy("auth-service")
        default = self._make_policy("*")
        config = PolicyConfig(repo_policies=[specific], default_policy=default)
        result = config.get_repo_policy("auth-service")
        assert result.repo_key == "auth-service"

    def test_wildcard_in_repo_policies_acts_as_fallback(self):
        wild = self._make_policy("*")
        config = PolicyConfig(repo_policies=[wild], default_policy=None)
        result = config.get_repo_policy("unknown-repo")
        assert result.repo_key == "*"

    def test_no_match_no_default_falls_back_to_builtin_default(self):
        # get_repo_policy never raises; it falls back to DEFAULT_REPO_POLICY
        config = PolicyConfig(repo_policies=[], default_policy=None)
        result = config.get_repo_policy("missing-repo")
        assert result is not None
        assert result.repo_key == "*"


# ---------------------------------------------------------------------------
# PolicyViolation
# ---------------------------------------------------------------------------


class TestPolicyViolation:
    def test_construction(self):
        v = PolicyViolation(
            rule_id="path.blocked",
            category="path",
            message="blocked",
            blocking=True,
        )
        assert v.rule_id == "path.blocked"
        assert v.blocking is True
        assert v.related_path is None

    def test_is_frozen(self):
        v = PolicyViolation(rule_id="x", category="y", message="z", blocking=True)
        with pytest.raises(ValidationError):
            v.blocking = False

    def test_non_blocking_violation(self):
        v = PolicyViolation(
            rule_id="review.required",
            category="review",
            message="needs review",
            blocking=False,
        )
        assert v.blocking is False

    def test_with_related_path(self):
        v = PolicyViolation(
            rule_id="path.blocked",
            category="path",
            message="blocked",
            blocking=True,
            related_path=".env",
        )
        assert v.related_path == ".env"


# ---------------------------------------------------------------------------
# PolicyWarning
# ---------------------------------------------------------------------------


class TestPolicyWarning:
    def test_construction(self):
        w = PolicyWarning(rule_id="branch.no_prefix_set", category="branch", message="no prefix")
        assert w.rule_id == "branch.no_prefix_set"

    def test_is_frozen(self):
        w = PolicyWarning(rule_id="x", category="y", message="z")
        with pytest.raises(ValidationError):
            w.message = "changed"


# ---------------------------------------------------------------------------
# PolicyDecision
# ---------------------------------------------------------------------------


class TestPolicyDecision:
    def _make_decision(self, status: PolicyStatus, **kw) -> PolicyDecision:
        return PolicyDecision(
            status=status,
            violations=kw.get("violations", []),
            warnings=kw.get("warnings", []),
            effective_validation_profile=kw.get("effective_validation_profile", "standard"),
            effective_review_requirement=kw.get("effective_review_requirement", "autonomous"),
            effective_scope=kw.get("effective_scope", []),
        )

    def test_is_allowed(self):
        d = self._make_decision(PolicyStatus.ALLOW)
        assert d.is_allowed is True
        assert d.is_blocked is False
        assert d.requires_review is False

    def test_is_blocked(self):
        d = self._make_decision(PolicyStatus.BLOCK)
        assert d.is_blocked is True
        assert d.is_allowed is False
        assert d.requires_review is False

    def test_requires_review(self):
        d = self._make_decision(PolicyStatus.REQUIRE_REVIEW)
        assert d.requires_review is True
        assert d.is_allowed is False
        assert d.is_blocked is False

    def test_allow_with_warnings_is_allowed(self):
        d = self._make_decision(PolicyStatus.ALLOW_WITH_WARNINGS)
        assert d.is_allowed is True
        assert d.is_blocked is False
        assert d.requires_review is False

    def test_is_frozen(self):
        d = self._make_decision(PolicyStatus.ALLOW)
        with pytest.raises(ValidationError):
            d.status = PolicyStatus.BLOCK

    def test_has_decision_id(self):
        d = self._make_decision(PolicyStatus.ALLOW)
        assert d.decision_id
        assert len(d.decision_id) > 8

    def test_has_evaluated_at(self):
        d = self._make_decision(PolicyStatus.ALLOW)
        assert d.evaluated_at is not None


# ---------------------------------------------------------------------------
# PolicyExplanation
# ---------------------------------------------------------------------------


class TestPolicyExplanation:
    def test_construction(self):
        e = PolicyExplanation(
            summary="ALLOWED",
            key_rules_applied=["rule.a"],
            review_reasoning="ok",
            validation_reasoning="ok",
            scope_reasoning="ok",
            routing_reasoning="ok",
        )
        assert e.summary == "ALLOWED"
        assert e.key_rules_applied == ["rule.a"]

    def test_is_frozen(self):
        e = PolicyExplanation(summary="x")
        with pytest.raises(ValidationError):
            e.summary = "y"

    def test_defaults_are_empty(self):
        e = PolicyExplanation(summary="x")
        assert e.key_rules_applied == []
        assert e.review_reasoning == ""
        assert e.routing_reasoning == ""
