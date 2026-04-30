# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
Unit tests for policy/explain.py — explain().

Verifies that explain() produces correct PolicyExplanation from
PolicyDecision objects with various states.
"""

from __future__ import annotations

import pytest

from operations_center.policy.engine import PolicyEngine
from operations_center.policy.explain import explain
from operations_center.policy.models import (
    PolicyDecision,
    PolicyExplanation,
    PolicyStatus,
    PolicyViolation,
    PolicyWarning,
)
from operations_center.contracts.enums import RiskLevel

from .conftest import (
    make_policy_config,
    make_proposal,
    make_repo_policy,
    remote_decision,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(
    status: PolicyStatus,
    violations: list[PolicyViolation] | None = None,
    warnings: list[PolicyWarning] | None = None,
    effective_validation_profile: str = "standard",
    effective_review_requirement: str = "autonomous",
    effective_scope: list[str] | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        status=status,
        violations=violations or [],
        warnings=warnings or [],
        effective_validation_profile=effective_validation_profile,
        effective_review_requirement=effective_review_requirement,
        effective_scope=effective_scope or [],
    )


def _blocking(rule_id: str, category: str, message: str) -> PolicyViolation:
    return PolicyViolation(rule_id=rule_id, category=category, message=message, blocking=True)


def _non_blocking(rule_id: str, category: str, message: str) -> PolicyViolation:
    return PolicyViolation(rule_id=rule_id, category=category, message=message, blocking=False)


def _warning(rule_id: str, category: str, message: str) -> PolicyWarning:
    return PolicyWarning(rule_id=rule_id, category=category, message=message)


# ---------------------------------------------------------------------------
# TestExplainReturnsExplanation
# ---------------------------------------------------------------------------


class TestExplainReturnsExplanation:
    def test_returns_policy_explanation_instance(self):
        decision = _make_decision(PolicyStatus.ALLOW)
        result = explain(decision)
        assert isinstance(result, PolicyExplanation)

    def test_is_frozen(self):
        from pydantic import ValidationError
        decision = _make_decision(PolicyStatus.ALLOW)
        result = explain(decision)
        with pytest.raises(ValidationError):
            result.summary = "changed"


# ---------------------------------------------------------------------------
# TestSummary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_allow_summary(self):
        decision = _make_decision(PolicyStatus.ALLOW)
        e = explain(decision)
        assert "ALLOWED" in e.summary
        assert "no policy restrictions" in e.summary.lower()

    def test_allow_with_warnings_summary(self):
        w = _warning("branch.no_prefix_set", "branch", "no branch prefix")
        decision = _make_decision(PolicyStatus.ALLOW_WITH_WARNINGS, warnings=[w])
        e = explain(decision)
        assert "ALLOWED WITH WARNINGS" in e.summary
        assert "no branch prefix" in e.summary

    def test_allow_with_warnings_no_warnings_list(self):
        decision = _make_decision(PolicyStatus.ALLOW_WITH_WARNINGS)
        e = explain(decision)
        assert "ALLOWED WITH WARNINGS" in e.summary

    def test_block_summary_with_blocking_violation(self):
        v = _blocking("path.blocked", "path", "Path '.ssh/id_rsa' is blocked")
        decision = _make_decision(PolicyStatus.BLOCK, violations=[v])
        e = explain(decision)
        assert "BLOCKED" in e.summary
        assert "'.ssh/id_rsa' is blocked" in e.summary

    def test_block_summary_no_violations(self):
        decision = _make_decision(PolicyStatus.BLOCK)
        e = explain(decision)
        assert "BLOCKED" in e.summary

    def test_require_review_summary_with_review_violation(self):
        v = _non_blocking("review.required", "review", "Human review required")
        decision = _make_decision(PolicyStatus.REQUIRE_REVIEW, violations=[v])
        e = explain(decision)
        assert "REVIEW REQUIRED" in e.summary
        assert "Human review required" in e.summary

    def test_require_review_summary_with_non_review_non_blocking(self):
        v = _non_blocking("path.review_required", "path", "sensitive path")
        decision = _make_decision(PolicyStatus.REQUIRE_REVIEW, violations=[v])
        e = explain(decision)
        assert "REVIEW REQUIRED" in e.summary

    def test_require_review_summary_no_violations(self):
        decision = _make_decision(PolicyStatus.REQUIRE_REVIEW)
        e = explain(decision)
        assert "REVIEW REQUIRED" in e.summary


# ---------------------------------------------------------------------------
# TestKeyRulesApplied
# ---------------------------------------------------------------------------


class TestKeyRulesApplied:
    def test_no_violations_no_warnings_empty_rules(self):
        decision = _make_decision(PolicyStatus.ALLOW)
        e = explain(decision)
        assert e.key_rules_applied == []

    def test_violation_rule_ids_included(self):
        v = _blocking("path.blocked", "path", "blocked")
        decision = _make_decision(PolicyStatus.BLOCK, violations=[v])
        e = explain(decision)
        assert "path.blocked" in e.key_rules_applied

    def test_warning_rule_ids_included(self):
        w = _warning("branch.no_prefix_set", "branch", "no prefix")
        decision = _make_decision(PolicyStatus.ALLOW_WITH_WARNINGS, warnings=[w])
        e = explain(decision)
        assert "branch.no_prefix_set" in e.key_rules_applied

    def test_no_duplicate_rule_ids(self):
        v = _blocking("path.blocked", "path", "blocked")
        w = _warning("path.blocked", "path", "also blocked")
        decision = _make_decision(PolicyStatus.BLOCK, violations=[v], warnings=[w])
        e = explain(decision)
        assert e.key_rules_applied.count("path.blocked") == 1

    def test_multiple_violations_all_included(self):
        v1 = _blocking("path.blocked", "path", "blocked")
        v2 = _blocking("tool.destructive_blocked", "tool", "destructive")
        decision = _make_decision(PolicyStatus.BLOCK, violations=[v1, v2])
        e = explain(decision)
        assert "path.blocked" in e.key_rules_applied
        assert "tool.destructive_blocked" in e.key_rules_applied


# ---------------------------------------------------------------------------
# TestReviewReasoning
# ---------------------------------------------------------------------------


class TestReviewReasoning:
    def test_autonomous_when_no_review_violations(self):
        decision = _make_decision(PolicyStatus.ALLOW, effective_review_requirement="autonomous")
        e = explain(decision)
        assert "autonomous" in e.review_reasoning.lower()

    def test_review_violation_message_in_reasoning(self):
        v = _non_blocking("review.required", "review", "High risk requires review")
        decision = _make_decision(PolicyStatus.REQUIRE_REVIEW, violations=[v])
        e = explain(decision)
        assert "High risk requires review" in e.review_reasoning

    def test_multiple_review_violations_joined(self):
        v1 = _non_blocking("review.required", "review", "high risk")
        v2 = _non_blocking("review.also", "review", "task type")
        decision = _make_decision(PolicyStatus.REQUIRE_REVIEW, violations=[v1, v2])
        e = explain(decision)
        assert "high risk" in e.review_reasoning
        assert "task type" in e.review_reasoning


# ---------------------------------------------------------------------------
# TestValidationReasoning
# ---------------------------------------------------------------------------


class TestValidationReasoning:
    def test_default_profile_when_no_violations(self):
        decision = _make_decision(PolicyStatus.ALLOW, effective_validation_profile="strict")
        e = explain(decision)
        assert "strict" in e.validation_reasoning

    def test_validation_violation_in_reasoning(self):
        v = _blocking("validation.required_unavailable", "validation", "no validation configured")
        decision = _make_decision(PolicyStatus.BLOCK, violations=[v])
        e = explain(decision)
        assert "no validation configured" in e.validation_reasoning

    def test_validation_warning_in_reasoning(self):
        w = _warning("validation.recommended_unavailable", "validation", "validation recommended")
        decision = _make_decision(PolicyStatus.ALLOW_WITH_WARNINGS, warnings=[w])
        e = explain(decision)
        assert "validation recommended" in e.validation_reasoning


# ---------------------------------------------------------------------------
# TestScopeReasoning
# ---------------------------------------------------------------------------


class TestScopeReasoning:
    def test_no_scope_declared(self):
        decision = _make_decision(PolicyStatus.ALLOW, effective_scope=[])
        e = explain(decision)
        assert "no path restriction" in e.scope_reasoning.lower()

    def test_scope_listed(self):
        decision = _make_decision(
            PolicyStatus.ALLOW, effective_scope=["src/main.py", "tests/"]
        )
        e = explain(decision)
        assert "src/main.py" in e.scope_reasoning

    def test_path_violation_appended_to_scope(self):
        v = _blocking("path.blocked", "path", "path is blocked")
        decision = _make_decision(
            PolicyStatus.BLOCK,
            violations=[v],
            effective_scope=["src/x.py"],
        )
        e = explain(decision)
        assert "path is blocked" in e.scope_reasoning


# ---------------------------------------------------------------------------
# TestRoutingReasoning
# ---------------------------------------------------------------------------


class TestRoutingReasoning:
    def test_compatible_when_no_routing_violations(self):
        decision = _make_decision(PolicyStatus.ALLOW)
        e = explain(decision)
        assert "compatible" in e.routing_reasoning.lower()

    def test_routing_violation_in_reasoning(self):
        v = _blocking("routing.local_only_violated", "routing", "must use local lane")
        decision = _make_decision(PolicyStatus.BLOCK, violations=[v])
        e = explain(decision)
        assert "must use local lane" in e.routing_reasoning

    def test_network_tool_violation_in_routing_reasoning(self):
        v = _blocking("tool.network_local_only", "tool", "network is local only")
        decision = _make_decision(PolicyStatus.BLOCK, violations=[v])
        e = explain(decision)
        assert "network is local only" in e.routing_reasoning


# ---------------------------------------------------------------------------
# Integration: explain via engine
# ---------------------------------------------------------------------------


class TestExplainViaEngine:
    def _engine(self, **kw) -> PolicyEngine:
        policy = make_repo_policy(**kw)
        return PolicyEngine.from_config(make_policy_config(policy))

    def test_explain_allowed_decision(self):
        engine = self._engine()
        proposal = make_proposal(allowed_paths=["src/x.py"])
        policy_decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        e = explain(policy_decision)
        assert "ALLOWED" in e.summary
        assert e.key_rules_applied == []

    def test_explain_blocked_decision(self):
        engine = self._engine(enabled=False)
        proposal = make_proposal()
        policy_decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        e = explain(policy_decision)
        assert "BLOCKED" in e.summary
        assert "repo.disabled" in e.key_rules_applied

    def test_explain_review_decision(self):
        engine = self._engine(require_review_for_risk_levels=["high"])
        proposal = make_proposal(risk_level=RiskLevel.HIGH, allowed_paths=["src/x.py"])
        policy_decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        e = explain(policy_decision)
        assert "REVIEW REQUIRED" in e.summary
        assert "review.required" in e.key_rules_applied

    def test_explain_warning_decision(self):
        engine = self._engine()
        proposal = make_proposal(branch_prefix="", allowed_paths=["src/x.py"])
        policy_decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        e = explain(policy_decision)
        assert "ALLOWED WITH WARNINGS" in e.summary
        assert "branch.no_prefix_set" in e.key_rules_applied
