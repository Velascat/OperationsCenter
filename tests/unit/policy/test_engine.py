"""
Unit tests for policy/engine.py — PolicyEngine.evaluate().

Structure:
  TestAllowPath        — clean proposals that should result in ALLOW
  TestAllowWithWarnings — proposals that get ALLOW_WITH_WARNINGS
  TestRequireReview    — proposals that trigger REQUIRE_REVIEW (non-blocking)
  TestBlock            — proposals that trigger BLOCK (blocking violations)
  TestEffectiveFields  — effective_* fields on the returned PolicyDecision
  TestEarlyExit        — disabled repo short-circuits evaluation
  TestFromDefaults     — engine wired to DEFAULT_POLICY_CONFIG
"""

from __future__ import annotations

import pytest

from operations_center.contracts.enums import LaneName, BackendName, RiskLevel, TaskType
from operations_center.policy.engine import PolicyEngine
from operations_center.policy.models import (
    PathScopeRule,
    PolicyConfig,
    PolicyStatus,
    ValidationRequirement,
)

from .conftest import (
    local_decision,
    make_decision,
    make_policy_config,
    make_proposal,
    make_repo_policy,
    remote_decision,
)


# ---------------------------------------------------------------------------
# TestAllowPath
# ---------------------------------------------------------------------------


class TestAllowPath:
    def _engine(self, **kw) -> PolicyEngine:
        policy = make_repo_policy(**kw)
        return PolicyEngine.from_config(make_policy_config(policy))

    def test_low_risk_clean_proposal_is_allowed(self):
        engine = self._engine()
        proposal = make_proposal(risk_level=RiskLevel.LOW, allowed_paths=["src/foo.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.ALLOW
        assert decision.is_allowed

    def test_no_violations_no_warnings(self):
        engine = self._engine()
        proposal = make_proposal(allowed_paths=["src/util.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.violations == []
        assert decision.warnings == []

    def test_medium_risk_with_validation_commands_is_allowed(self):
        engine = self._engine()
        proposal = make_proposal(
            risk_level=RiskLevel.MEDIUM,
            validation_commands=["pytest"],
            allowed_paths=["src/service.py"],
        )
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.ALLOW

    def test_task_type_in_allowed_list_passes(self):
        engine = self._engine(allowed_task_types=["bug_fix", "lint_fix"])
        proposal = make_proposal(task_type=TaskType.BUG_FIX)
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.is_allowed

    def test_local_lane_with_local_only_label_is_allowed(self):
        engine = self._engine()
        proposal = make_proposal(labels=["local_only"], allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, local_decision(proposal.proposal_id))
        assert decision.is_allowed

    def test_path_matching_allow_rule_is_allowed(self):
        rule = PathScopeRule(path_pattern="src/**", access_mode="allow")
        engine = self._engine(path_rules=[rule])
        proposal = make_proposal(allowed_paths=["src/main.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.is_allowed

    def test_no_paths_with_allow_default_is_allowed(self):
        engine = self._engine(path_default_mode="allow")
        proposal = make_proposal(allowed_paths=[])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.is_allowed


# ---------------------------------------------------------------------------
# TestAllowWithWarnings
# ---------------------------------------------------------------------------


class TestAllowWithWarnings:
    def _engine(self, **kw) -> PolicyEngine:
        policy = make_repo_policy(**kw)
        return PolicyEngine.from_config(make_policy_config(policy))

    def test_missing_branch_prefix_emits_warning(self):
        engine = self._engine()
        proposal = make_proposal(branch_prefix="", allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.ALLOW_WITH_WARNINGS
        rule_ids = [w.rule_id for w in decision.warnings]
        assert "branch.no_prefix_set" in rule_ids

    def test_validation_recommended_but_unavailable_emits_warning(self):
        vr = ValidationRequirement(
            applies_to_risk_levels=["medium"],
            required_profile="standard",
            must_pass=True,
            block_if_unavailable=False,
        )
        engine = self._engine(validation_requirements=[vr])
        proposal = make_proposal(
            risk_level=RiskLevel.MEDIUM,
            validation_commands=[],
            allowed_paths=["src/x.py"],
        )
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.ALLOW_WITH_WARNINGS
        rule_ids = [w.rule_id for w in decision.warnings]
        assert "validation.recommended_unavailable" in rule_ids

    def test_status_is_allow_with_warnings_not_block(self):
        engine = self._engine()
        proposal = make_proposal(branch_prefix="")
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert not decision.is_blocked
        assert decision.is_allowed


# ---------------------------------------------------------------------------
# TestRequireReview
# ---------------------------------------------------------------------------


class TestRequireReview:
    def _engine(self, **kw) -> PolicyEngine:
        policy = make_repo_policy(**kw)
        return PolicyEngine.from_config(make_policy_config(policy))

    def test_high_risk_requires_review_by_default(self):
        engine = self._engine(require_review_for_risk_levels=["high"])
        proposal = make_proposal(risk_level=RiskLevel.HIGH, allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.REQUIRE_REVIEW

    def test_feature_task_type_requires_review(self):
        engine = self._engine(require_review_for_task_types=["feature"])
        proposal = make_proposal(task_type=TaskType.FEATURE, allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.REQUIRE_REVIEW

    def test_sensitive_path_triggers_review(self):
        rule = PathScopeRule(path_pattern="**/migrations/**", access_mode="review_required")
        engine = self._engine(path_rules=[rule])
        proposal = make_proposal(allowed_paths=["db/migrations/0001_init.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.REQUIRE_REVIEW

    def test_env_file_triggers_review(self):
        rule = PathScopeRule(path_pattern=".env*", access_mode="review_required")
        engine = self._engine(path_rules=[rule])
        proposal = make_proposal(allowed_paths=[".env.production"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.REQUIRE_REVIEW

    def test_review_required_label_triggers_review(self):
        engine = self._engine()
        proposal = make_proposal(labels=["review_required"], allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.REQUIRE_REVIEW

    def test_pr_required_but_not_set_triggers_review(self):
        engine = self._engine(require_pr=True)
        proposal = make_proposal(open_pr=False, allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.REQUIRE_REVIEW
        rule_ids = [v.rule_id for v in decision.violations]
        assert "branch.pr_required" in rule_ids

    def test_autonomous_not_allowed_triggers_review(self):
        engine = self._engine(autonomous_allowed=False)
        proposal = make_proposal(allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.REQUIRE_REVIEW

    def test_review_violations_are_non_blocking(self):
        engine = self._engine(require_review_for_risk_levels=["high"])
        proposal = make_proposal(risk_level=RiskLevel.HIGH, allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert all(not v.blocking for v in decision.violations)

    def test_requires_review_is_not_blocked(self):
        engine = self._engine(require_review_for_risk_levels=["high"])
        proposal = make_proposal(risk_level=RiskLevel.HIGH, allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert not decision.is_blocked
        assert decision.requires_review


# ---------------------------------------------------------------------------
# TestBlock
# ---------------------------------------------------------------------------


class TestBlock:
    def _engine(self, **kw) -> PolicyEngine:
        policy = make_repo_policy(**kw)
        return PolicyEngine.from_config(make_policy_config(policy))

    def test_disabled_repo_is_blocked(self):
        engine = self._engine(enabled=False)
        proposal = make_proposal()
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK
        assert decision.is_blocked

    def test_blocked_task_type_is_blocked(self):
        engine = self._engine(blocked_task_types=["feature"])
        proposal = make_proposal(task_type=TaskType.FEATURE)
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK

    def test_task_not_in_allowlist_is_blocked(self):
        engine = self._engine(allowed_task_types=["lint_fix"])
        proposal = make_proposal(task_type=TaskType.BUG_FIX)
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK
        rule_ids = [v.rule_id for v in decision.violations]
        assert "task_type.not_in_allowlist" in rule_ids

    def test_local_only_label_with_remote_lane_is_blocked(self):
        engine = self._engine()
        proposal = make_proposal(labels=["local_only"], allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK
        rule_ids = [v.rule_id for v in decision.violations]
        assert "routing.local_only_violated" in rule_ids

    def test_no_remote_label_with_remote_lane_is_blocked(self):
        engine = self._engine()
        proposal = make_proposal(labels=["no_remote"], allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK

    def test_path_blocked_rule_is_blocked(self):
        rule = PathScopeRule(path_pattern=".ssh/**", access_mode="block")
        engine = self._engine(path_rules=[rule])
        proposal = make_proposal(allowed_paths=[".ssh/id_rsa"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK
        rule_ids = [v.rule_id for v in decision.violations]
        assert "path.blocked" in rule_ids

    def test_default_path_mode_block_with_no_paths_is_blocked(self):
        engine = self._engine(path_default_mode="block")
        proposal = make_proposal(allowed_paths=[])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK

    def test_unmatched_path_with_default_block_is_blocked(self):
        rule = PathScopeRule(path_pattern="src/**", access_mode="allow")
        engine = self._engine(path_rules=[rule], path_default_mode="block")
        proposal = make_proposal(allowed_paths=["vendor/lib.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK

    def test_network_mode_blocked_is_blocked(self):
        engine = self._engine(network_mode="blocked")
        proposal = make_proposal(allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK
        rule_ids = [v.rule_id for v in decision.violations]
        assert "tool.network_blocked" in rule_ids

    def test_network_local_only_with_remote_lane_is_blocked(self):
        engine = self._engine(network_mode="local_only")
        proposal = make_proposal(allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK
        rule_ids = [v.rule_id for v in decision.violations]
        assert "tool.network_local_only" in rule_ids

    def test_network_local_only_with_local_lane_is_ok(self):
        engine = self._engine(network_mode="local_only")
        proposal = make_proposal(allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, local_decision(proposal.proposal_id))
        assert decision.status != PolicyStatus.BLOCK

    def test_destructive_label_blocked(self):
        engine = self._engine(allow_destructive_actions=False)
        proposal = make_proposal(labels=["rm_rf"], allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK
        rule_ids = [v.rule_id for v in decision.violations]
        assert "tool.destructive_blocked" in rule_ids

    def test_force_push_label_blocked(self):
        engine = self._engine(allow_destructive_actions=False)
        proposal = make_proposal(labels=["force_push"], allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK

    def test_drop_table_label_blocked(self):
        engine = self._engine(allow_destructive_actions=False)
        proposal = make_proposal(labels=["drop_table"], allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK

    def test_validation_required_unavailable_blocks(self):
        vr = ValidationRequirement(
            applies_to_risk_levels=["high"],
            required_profile="strict",
            must_pass=True,
            block_if_unavailable=True,
        )
        engine = self._engine(validation_requirements=[vr])
        proposal = make_proposal(
            risk_level=RiskLevel.HIGH,
            validation_commands=[],
            allowed_paths=["src/x.py"],
        )
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        # validation block should appear — though review may also be present
        blocking_rule_ids = [v.rule_id for v in decision.violations if v.blocking]
        assert "validation.required_unavailable" in blocking_rule_ids

    def test_base_branch_not_allowed_blocks(self):
        engine = self._engine(allowed_base_branches=["main"])
        proposal = make_proposal(base_branch="develop", allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK
        rule_ids = [v.rule_id for v in decision.violations]
        assert "branch.base_branch_not_allowed" in rule_ids

    def test_blocked_without_human_blocks(self):
        engine = self._engine(blocked_without_human=True)
        proposal = make_proposal(allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.status == PolicyStatus.BLOCK
        rule_ids = [v.rule_id for v in decision.violations]
        assert "review.blocked_without_human" in rule_ids

    def test_blocked_violations_are_blocking(self):
        engine = self._engine(enabled=False)
        proposal = make_proposal()
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert all(v.blocking for v in decision.violations)


# ---------------------------------------------------------------------------
# TestEarlyExit
# ---------------------------------------------------------------------------


class TestEarlyExit:
    def test_disabled_repo_skips_other_checks(self):
        policy = make_repo_policy(
            enabled=False,
            require_review_for_risk_levels=["low"],
            network_mode="blocked",
        )
        engine = PolicyEngine.from_config(make_policy_config(policy))
        proposal = make_proposal()
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        # Only the repo.disabled violation should be present
        assert len(decision.violations) == 1
        assert decision.violations[0].rule_id == "repo.disabled"


# ---------------------------------------------------------------------------
# TestEffectiveFields
# ---------------------------------------------------------------------------


class TestEffectiveFields:
    def _engine(self, **kw) -> PolicyEngine:
        policy = make_repo_policy(**kw)
        return PolicyEngine.from_config(make_policy_config(policy))

    def test_effective_scope_reflects_allowed_paths(self):
        engine = self._engine()
        paths = ["src/main.py", "tests/test_main.py"]
        proposal = make_proposal(allowed_paths=paths)
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.effective_scope == paths

    def test_effective_review_requirement_autonomous_when_no_review_needed(self):
        engine = self._engine()
        proposal = make_proposal(risk_level=RiskLevel.LOW, allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.effective_review_requirement == "autonomous"

    def test_effective_review_requirement_required_when_review_needed(self):
        engine = self._engine(require_review_for_risk_levels=["high"])
        proposal = make_proposal(risk_level=RiskLevel.HIGH, allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.effective_review_requirement == "required"

    def test_effective_validation_profile_from_matching_requirement(self):
        vr = ValidationRequirement(
            applies_to_risk_levels=["high"],
            required_profile="strict",
        )
        engine = self._engine(validation_requirements=[vr])
        proposal = make_proposal(
            risk_level=RiskLevel.HIGH,
            validation_commands=["pytest"],
            allowed_paths=["src/x.py"],
        )
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.effective_validation_profile == "strict"

    def test_effective_validation_profile_default_when_no_match(self):
        engine = self._engine()
        proposal = make_proposal(allowed_paths=["src/x.py"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.effective_validation_profile == "standard"

    def test_decision_has_decision_id(self):
        engine = self._engine()
        proposal = make_proposal()
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.decision_id

    def test_decision_has_evaluated_at(self):
        engine = self._engine()
        proposal = make_proposal()
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.evaluated_at is not None


# ---------------------------------------------------------------------------
# TestFromDefaults
# ---------------------------------------------------------------------------


class TestFromDefaults:
    def test_from_defaults_instantiates(self):
        engine = PolicyEngine.from_defaults()
        assert engine is not None

    def test_low_risk_bug_fix_is_allowed(self):
        engine = PolicyEngine.from_defaults()
        proposal = make_proposal(
            task_type=TaskType.BUG_FIX,
            risk_level=RiskLevel.LOW,
            allowed_paths=["src/helper.py"],
        )
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.is_allowed

    def test_ssh_key_path_is_blocked(self):
        engine = PolicyEngine.from_defaults()
        # **/.ssh/** pattern requires a directory prefix (fnmatch semantics)
        proposal = make_proposal(allowed_paths=["home/user/.ssh/id_rsa"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.is_blocked

    def test_migration_path_triggers_review(self):
        engine = PolicyEngine.from_defaults()
        proposal = make_proposal(
            task_type=TaskType.BUG_FIX,
            risk_level=RiskLevel.LOW,
            allowed_paths=["db/migrations/0001_init.py"],
        )
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.requires_review

    def test_high_risk_feature_requires_review(self):
        engine = PolicyEngine.from_defaults()
        proposal = make_proposal(
            task_type=TaskType.FEATURE,
            risk_level=RiskLevel.HIGH,
            allowed_paths=["src/new_feature.py"],
            validation_commands=["pytest"],
        )
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.requires_review

    def test_env_file_triggers_review(self):
        engine = PolicyEngine.from_defaults()
        proposal = make_proposal(allowed_paths=[".env.production"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.requires_review

    def test_workflow_file_triggers_review(self):
        engine = PolicyEngine.from_defaults()
        proposal = make_proposal(allowed_paths=[".github/workflows/ci.yml"])
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.requires_review

    def test_rm_rf_label_is_blocked(self):
        engine = PolicyEngine.from_defaults()
        proposal = make_proposal(
            labels=["rm_rf"],
            allowed_paths=["src/x.py"],
        )
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        assert decision.is_blocked

    def test_high_risk_no_validation_blocks(self):
        engine = PolicyEngine.from_defaults()
        proposal = make_proposal(
            risk_level=RiskLevel.HIGH,
            validation_commands=[],
            allowed_paths=["src/x.py"],
        )
        decision = engine.evaluate(proposal, remote_decision(proposal.proposal_id))
        blocking = [v for v in decision.violations if v.blocking]
        assert any(v.rule_id == "validation.required_unavailable" for v in blocking)
