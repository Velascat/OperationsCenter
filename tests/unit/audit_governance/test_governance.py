"""Tests for Phase 12 audit governance.

Covers: request validation, policy checks, decision logic, budget/cooldown state,
manual approval, governed runner (with monkeypatched dispatch), report persistence,
import boundaries.
"""

from __future__ import annotations

import ast
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from operations_center.audit_governance import (
    AuditBudgetState,
    AuditCooldownState,
    AuditGovernanceDecision,
    AuditGovernanceRequest,
    AuditGovernedRunResult,
    AuditManualApproval,
    BudgetConfig,
    CooldownConfig,
    GovernanceConfig,
    GovernanceReportError,
    ManualApprovalError,
    evaluate_governance_policies,
    increment_budget_after_dispatch,
    load_budget_state,
    load_cooldown_state,
    load_governance_report,
    make_governance_decision,
    make_manual_approval,
    run_governed_audit,
    update_cooldown_after_dispatch,
    validate_manual_approval,
    write_governance_report,
)
from operations_center.audit_governance.models import AuditGovernanceReport
from operations_center.audit_dispatch.models import DispatchStatus, ManagedAuditDispatchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(**kwargs) -> AuditGovernanceRequest:
    defaults = dict(
        repo_id="videofoundry",
        audit_type="representative",
        requested_by="alice",
        requested_reason="fixture refresh after pipeline change",
        urgency="normal",
    )
    defaults.update(kwargs)
    return AuditGovernanceRequest(**defaults)


def _make_config(**kwargs) -> GovernanceConfig:
    defaults = dict(
        known_repos=["videofoundry"],
        known_audit_types={"videofoundry": ["representative", "enrichment"]},
    )
    defaults.update(kwargs)
    return GovernanceConfig(**defaults)


def _make_dispatch_result(*, succeeded: bool = True) -> ManagedAuditDispatchResult:
    now = datetime.now(UTC)
    return ManagedAuditDispatchResult(
        repo_id="videofoundry",
        audit_type="representative",
        run_id="test_run_001",
        status=DispatchStatus.COMPLETED if succeeded else DispatchStatus.FAILED,
        started_at=now,
        ended_at=now,
        duration_seconds=1.0,
    )


# ---------------------------------------------------------------------------
# Contract 1 — Governance request validation
# ---------------------------------------------------------------------------

class TestGovernanceRequest:
    def test_valid_request_creates_successfully(self):
        req = _make_request()
        assert req.repo_id == "videofoundry"
        assert req.audit_type == "representative"
        assert req.requested_by == "alice"
        assert req.urgency == "normal"

    def test_request_id_is_path_safe(self):
        req = _make_request()
        # request_id should not contain characters that break file paths
        assert "/" not in req.request_id
        assert " " not in req.request_id

    def test_empty_repo_id_rejected(self):
        with pytest.raises(Exception, match="repo_id"):
            _make_request(repo_id="")

    def test_empty_audit_type_rejected(self):
        with pytest.raises(Exception, match="audit_type"):
            _make_request(audit_type="")

    def test_empty_requested_reason_rejected(self):
        with pytest.raises(Exception, match="requested_reason"):
            _make_request(requested_reason="")

    def test_empty_requested_by_rejected(self):
        with pytest.raises(Exception, match="requested_by"):
            _make_request(requested_by="")

    def test_urgency_values(self):
        for urgency in ("low", "normal", "high", "urgent"):
            req = _make_request(urgency=urgency)
            assert req.urgency == urgency

    def test_recommendation_ids_are_context_only(self):
        req = _make_request(related_recommendation_ids=["rec-001", "rec-002"])
        assert req.related_recommendation_ids == ["rec-001", "rec-002"]
        # recommendations do not approve — just stored as context

    def test_frozen(self):
        req = _make_request()
        with pytest.raises(Exception):
            req.repo_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Contract 2 — Governance decision model
# ---------------------------------------------------------------------------

class TestGovernanceDecision:
    def test_decision_records_explicit_decision(self):
        req = _make_request()
        policy_results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, policy_results)
        assert decision.decision in ("approved", "denied", "needs_manual_approval", "deferred")

    def test_is_approved_property(self):
        req = _make_request(
            urgency="normal",
            related_suite_report_path="/some/report.json",
        )
        cfg = _make_config()
        results = evaluate_governance_policies(
            req,
            known_repos=cfg.known_repos,
            known_audit_types=cfg.known_audit_types,
        )
        decision = make_governance_decision(req, results)
        assert decision.is_approved == (decision.decision == "approved")

    def test_is_denied_property(self):
        req = _make_request(repo_id="unknown_repo")
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, results)
        assert decision.is_denied
        assert decision.decision == "denied"

    def test_failed_policies_property(self):
        req = _make_request(repo_id="unknown_repo")
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={},
        )
        decision = make_governance_decision(req, results)
        assert any(p.policy_name == "known_repo_required" for p in decision.failed_policies)


# ---------------------------------------------------------------------------
# Contract 3 — Policy checks
# ---------------------------------------------------------------------------

class TestPolicyChecks:
    def test_known_repo_passes(self):
        req = _make_request(related_suite_report_path="/suite.json")
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        by_name = {p.policy_name: p for p in results}
        assert by_name["known_repo_required"].status == "passed"

    def test_unknown_repo_denies(self):
        req = _make_request(repo_id="nonexistent_repo")
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, results)
        assert decision.decision == "denied"
        by_name = {p.policy_name: p for p in results}
        assert by_name["known_repo_required"].status == "failed"

    def test_known_audit_type_passes(self):
        req = _make_request(related_suite_report_path="/suite.json")
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        by_name = {p.policy_name: p for p in results}
        assert by_name["known_audit_type_required"].status == "passed"

    def test_unknown_audit_type_denies(self):
        req = _make_request(audit_type="nonexistent_type")
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, results)
        assert decision.decision == "denied"

    def test_cooldown_active_defers_low_urgency(self):
        req = _make_request(urgency="normal", related_suite_report_path="/suite.json")
        cooldown_state = AuditCooldownState(
            repo_id="videofoundry",
            audit_type="representative",
            cooldown_seconds=3600,
            last_run_at=datetime.now(UTC),  # just ran — in cooldown
        )
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
            cooldown_state=cooldown_state,
        )
        decision = make_governance_decision(req, results)
        assert decision.decision == "deferred"

    def test_cooldown_inactive_passes(self):
        req = _make_request(related_suite_report_path="/suite.json")
        cooldown_state = AuditCooldownState(
            repo_id="videofoundry",
            audit_type="representative",
            cooldown_seconds=60,
            last_run_at=datetime.now(UTC) - timedelta(seconds=120),  # expired
        )
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
            cooldown_state=cooldown_state,
        )
        by_name = {p.policy_name: p for p in results}
        assert by_name["cooldown_policy"].status == "passed"

    def test_budget_exhausted_defers_low_urgency(self):
        now = datetime.now(UTC)
        req = _make_request(urgency="normal", related_suite_report_path="/suite.json")
        budget_state = AuditBudgetState(
            repo_id="videofoundry",
            audit_type="representative",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=6),
            max_runs=5,
            runs_used=5,  # exhausted
        )
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
            budget_state=budget_state,
        )
        decision = make_governance_decision(req, results)
        assert decision.decision == "deferred"

    def test_budget_available_passes(self):
        now = datetime.now(UTC)
        req = _make_request(related_suite_report_path="/suite.json")
        budget_state = AuditBudgetState(
            repo_id="videofoundry",
            audit_type="representative",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=6),
            max_runs=10,
            runs_used=3,
        )
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
            budget_state=budget_state,
        )
        by_name = {p.policy_name: p for p in results}
        assert by_name["budget_policy"].status == "passed"

    def test_mini_regression_missing_normal_urgency_needs_approval(self):
        req = _make_request(urgency="normal", related_suite_report_path=None)
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
            require_mini_regression_for_urgency=["low", "normal"],
        )
        decision = make_governance_decision(req, results)
        assert decision.decision == "needs_manual_approval"

    def test_mini_regression_present_passes(self):
        req = _make_request(related_suite_report_path="/suite.json")
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        by_name = {p.policy_name: p for p in results}
        assert by_name["mini_regression_first_policy"].status == "passed"

    def test_urgent_override_requires_manual_approval(self):
        req = _make_request(urgency="urgent", related_suite_report_path="/suite.json")
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, results)
        assert decision.decision == "needs_manual_approval"
        assert decision.requires_manual_approval is True

    def test_high_urgency_also_requires_manual_approval(self):
        req = _make_request(urgency="high", related_suite_report_path="/suite.json")
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, results)
        assert decision.decision == "needs_manual_approval"


# ---------------------------------------------------------------------------
# Contract 4 — Mini regression first rule
# ---------------------------------------------------------------------------

class TestMiniRegressionFirstRule:
    def test_suite_report_present_passes(self):
        req = _make_request(
            urgency="low",
            related_suite_report_path="/path/to/suite_report.json",
        )
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        by_name = {p.policy_name: p for p in results}
        assert by_name["mini_regression_first_policy"].status == "passed"

    def test_no_suite_report_low_urgency_needs_approval(self):
        req = _make_request(urgency="low")
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, results)
        assert decision.decision == "needs_manual_approval"

    def test_no_suite_report_urgent_is_warning_not_denied(self):
        req = _make_request(urgency="urgent", related_suite_report_path=None)
        results = evaluate_governance_policies(
            req,
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        by_name = {p.policy_name: p for p in results}
        # urgent without mini_regression evidence is a warning, not a hard fail
        assert by_name["mini_regression_first_policy"].status == "warning"


# ---------------------------------------------------------------------------
# Contract 5 — Budget state tracking
# ---------------------------------------------------------------------------

class TestBudgetState:
    def test_fresh_budget_created_when_no_state(self, tmp_path: Path):
        cfg = BudgetConfig(max_runs=5, period_days=7)
        state = load_budget_state(tmp_path, "videofoundry", "representative", cfg)
        assert state.runs_used == 0
        assert state.max_runs == 5
        assert not state.is_exhausted

    def test_budget_increments_after_dispatch(self, tmp_path: Path):
        cfg = BudgetConfig(max_runs=5, period_days=7)
        state = increment_budget_after_dispatch(
            tmp_path, "videofoundry", "representative", cfg
        )
        assert state.runs_used == 1
        assert state.runs_remaining == 4

    def test_budget_exhaustion_detected(self, tmp_path: Path):
        cfg = BudgetConfig(max_runs=2, period_days=7)
        increment_budget_after_dispatch(tmp_path, "videofoundry", "representative", cfg)
        state = increment_budget_after_dispatch(tmp_path, "videofoundry", "representative", cfg)
        assert state.is_exhausted
        assert state.runs_remaining == 0

    def test_budget_period_rollover(self, tmp_path: Path):
        cfg = BudgetConfig(max_runs=5, period_days=7)
        # Manually write an expired state
        now = datetime.now(UTC)
        expired = AuditBudgetState(
            repo_id="videofoundry",
            audit_type="representative",
            period_start=now - timedelta(days=14),
            period_end=now - timedelta(days=7),  # expired
            max_runs=5,
            runs_used=5,
        )
        state_path = tmp_path / "videofoundry__representative__budget.json"
        state_path.write_text(expired.model_dump_json(), encoding="utf-8")

        # Load should roll over to fresh period
        state = load_budget_state(tmp_path, "videofoundry", "representative", cfg)
        assert state.runs_used == 0
        assert not state.is_exhausted


# ---------------------------------------------------------------------------
# Contract 6 — Cooldown state tracking
# ---------------------------------------------------------------------------

class TestCooldownState:
    def test_fresh_cooldown_no_restriction(self, tmp_path: Path):
        cfg = CooldownConfig(cooldown_seconds=3600)
        state = load_cooldown_state(tmp_path, "videofoundry", "representative", cfg)
        assert not state.is_in_cooldown()

    def test_cooldown_active_after_dispatch(self, tmp_path: Path):
        cfg = CooldownConfig(cooldown_seconds=3600)
        state = update_cooldown_after_dispatch(tmp_path, "videofoundry", "representative", cfg)
        assert state.is_in_cooldown()
        assert state.seconds_remaining() > 0

    def test_cooldown_expires(self):
        state = AuditCooldownState(
            repo_id="videofoundry",
            audit_type="representative",
            cooldown_seconds=60,
            last_run_at=datetime.now(UTC) - timedelta(seconds=120),
        )
        assert not state.is_in_cooldown()
        assert state.seconds_remaining() == 0.0

    def test_cooldown_state_persisted(self, tmp_path: Path):
        cfg = CooldownConfig(cooldown_seconds=3600)
        update_cooldown_after_dispatch(tmp_path, "videofoundry", "representative", cfg)
        loaded = load_cooldown_state(tmp_path, "videofoundry", "representative", cfg)
        assert loaded.last_run_at is not None
        assert loaded.is_in_cooldown()


# ---------------------------------------------------------------------------
# Contract 7 — Manual approval
# ---------------------------------------------------------------------------

class TestManualApproval:
    def test_valid_approval_references_decision_and_request(self):
        req = _make_request(urgency="urgent", related_suite_report_path="/suite.json")
        results = evaluate_governance_policies(
            req, known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, results)
        approval = make_manual_approval(decision, req, approved_by="bob")
        assert approval.decision_id == decision.decision_id
        assert approval.request_id == req.request_id
        assert approval.approved_by == "bob"

    def test_mismatched_decision_id_raises(self):
        req = _make_request(urgency="urgent", related_suite_report_path="/suite.json")
        results = evaluate_governance_policies(
            req, known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, results)
        wrong_approval = AuditManualApproval(
            decision_id="wrong_id",
            request_id=req.request_id,
            approved_by="bob",
        )
        with pytest.raises(ManualApprovalError, match="decision_id"):
            validate_manual_approval(wrong_approval, decision, req)

    def test_mismatched_request_id_raises(self):
        req = _make_request(urgency="urgent", related_suite_report_path="/suite.json")
        results = evaluate_governance_policies(
            req, known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, results)
        wrong_approval = AuditManualApproval(
            decision_id=decision.decision_id,
            request_id="wrong_request_id",
            approved_by="bob",
        )
        with pytest.raises(ManualApprovalError, match="request_id"):
            validate_manual_approval(wrong_approval, decision, req)

    def test_cannot_approve_denied_decision(self):
        req = _make_request(repo_id="unknown_repo")
        results = evaluate_governance_policies(
            req, known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, results)
        assert decision.decision == "denied"
        approval = AuditManualApproval(
            decision_id=decision.decision_id,
            request_id=req.request_id,
            approved_by="bob",
        )
        with pytest.raises(ManualApprovalError):
            validate_manual_approval(approval, decision, req)


# ---------------------------------------------------------------------------
# Contract 8 — Governed runner dispatch behavior
# ---------------------------------------------------------------------------

_DISPATCH_TARGET = "operations_center.audit_governance.runner.dispatch_managed_audit"


class TestGovernedRunner:
    def test_approved_decision_calls_dispatch(self, tmp_path: Path):
        req = _make_request(
            urgency="normal",
            related_suite_report_path="/suite.json",
        )
        cfg = _make_config(state_dir=tmp_path / "state")
        dispatch_mock = MagicMock(return_value=_make_dispatch_result())

        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        assert dispatch_mock.called
        assert result.governance_status == "approved_and_dispatched"
        assert result.dispatch_result is not None

    def test_denied_decision_does_not_call_dispatch(self, tmp_path: Path):
        req = _make_request(repo_id="unknown_repo")
        cfg = _make_config(state_dir=tmp_path / "state")
        dispatch_mock = MagicMock()

        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        assert not dispatch_mock.called
        assert result.governance_status == "denied"
        assert result.dispatch_result is None

    def test_deferred_does_not_call_dispatch(self, tmp_path: Path):
        req = _make_request(urgency="normal", related_suite_report_path="/suite.json")
        # Put in cooldown
        cooldown_cfg = CooldownConfig(cooldown_seconds=7200)
        update_cooldown_after_dispatch(
            tmp_path / "state", "videofoundry", "representative", cooldown_cfg
        )
        cfg = _make_config(
            state_dir=tmp_path / "state",
            cooldown_config={"videofoundry": {"representative": cooldown_cfg}},
        )
        dispatch_mock = MagicMock()

        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        assert not dispatch_mock.called
        assert result.governance_status == "deferred"

    def test_needs_manual_approval_without_approval_does_not_dispatch(self, tmp_path: Path):
        req = _make_request(urgency="urgent", related_suite_report_path="/suite.json")
        cfg = _make_config(state_dir=tmp_path / "state")
        dispatch_mock = MagicMock()

        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        assert not dispatch_mock.called
        assert result.governance_status == "needs_manual_approval"

    def test_recommendations_do_not_call_dispatch(self, tmp_path: Path):
        # recommendations are context-only; they cannot cause dispatch
        req = _make_request(
            urgency="urgent",  # urgent → needs_manual_approval
            related_recommendation_ids=["rec-001", "rec-002"],
            related_suite_report_path="/suite.json",
        )
        cfg = _make_config(state_dir=tmp_path / "state")
        dispatch_mock = MagicMock()

        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        assert not dispatch_mock.called

    def test_approved_with_manual_approval_dispatches(self, tmp_path: Path):
        req = _make_request(urgency="urgent", related_suite_report_path="/suite.json")
        cfg = _make_config(state_dir=tmp_path / "state")
        results = evaluate_governance_policies(
            req, known_repos=cfg.known_repos, known_audit_types=cfg.known_audit_types
        )
        decision = make_governance_decision(req, results)
        assert decision.requires_manual_approval

        approval = make_manual_approval(decision, req, approved_by="bob")
        dispatch_mock = MagicMock(return_value=_make_dispatch_result())

        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(
                req, approval=approval, governance_config=cfg, output_dir=tmp_path / "out"
            )

        assert dispatch_mock.called
        assert result.governance_status == "approved_and_dispatched"

    def test_dispatch_failure_returns_dispatch_failed_status(self, tmp_path: Path):
        req = _make_request(urgency="normal", related_suite_report_path="/suite.json")
        cfg = _make_config(state_dir=tmp_path / "state")
        dispatch_mock = MagicMock(side_effect=RuntimeError("subprocess failed"))

        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        assert result.governance_status == "dispatch_failed"
        assert result.dispatch_result is None


# ---------------------------------------------------------------------------
# Contract 9 — Budget/cooldown updates only after dispatch
# ---------------------------------------------------------------------------

class TestStateUpdatesAfterDispatch:
    def test_budget_updated_only_after_dispatch(self, tmp_path: Path):
        req = _make_request(urgency="normal", related_suite_report_path="/suite.json")
        cfg = _make_config(state_dir=tmp_path / "state")
        dispatch_mock = MagicMock(return_value=_make_dispatch_result())

        # No state before dispatch
        budget_cfg = BudgetConfig()
        state_before = load_budget_state(tmp_path / "state", "videofoundry", "representative", budget_cfg)
        assert state_before.runs_used == 0

        with patch(_DISPATCH_TARGET, dispatch_mock):
            run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        state_after = load_budget_state(tmp_path / "state", "videofoundry", "representative", budget_cfg)
        assert state_after.runs_used == 1

    def test_budget_not_updated_when_denied(self, tmp_path: Path):
        req = _make_request(repo_id="unknown_repo")
        cfg = _make_config(state_dir=tmp_path / "state")

        with patch(_DISPATCH_TARGET, MagicMock()):
            run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        budget_cfg = BudgetConfig()
        state = load_budget_state(tmp_path / "state", "unknown_repo", "representative", budget_cfg)
        assert state.runs_used == 0


# ---------------------------------------------------------------------------
# Contract 10 — Governance report persistence
# ---------------------------------------------------------------------------

class TestReportPersistence:
    def test_report_written_for_approved_run(self, tmp_path: Path):
        req = _make_request(urgency="normal", related_suite_report_path="/suite.json")
        cfg = _make_config(state_dir=tmp_path / "state")
        dispatch_mock = MagicMock(return_value=_make_dispatch_result())

        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        assert result.report_path
        assert Path(result.report_path).exists()

    def test_report_written_for_denied_request(self, tmp_path: Path):
        req = _make_request(repo_id="unknown_repo")
        cfg = _make_config(state_dir=tmp_path / "state")

        result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        assert result.report_path
        assert Path(result.report_path).exists()

    def test_report_load_roundtrip(self, tmp_path: Path):
        req = _make_request(urgency="normal", related_suite_report_path="/suite.json")
        cfg = _make_config(state_dir=tmp_path / "state")
        dispatch_mock = MagicMock(return_value=_make_dispatch_result())

        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        loaded = load_governance_report(result.report_path)
        assert loaded.request.request_id == req.request_id
        assert loaded.decision.decision == result.decision.decision

    def test_load_nonexistent_report_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_governance_report(tmp_path / "nonexistent.json")

    def test_load_invalid_json_raises(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        with pytest.raises(GovernanceReportError, match="not valid JSON"):
            load_governance_report(bad)


# ---------------------------------------------------------------------------
# Contract 11 — Import boundary
# ---------------------------------------------------------------------------

class TestImportBoundary:
    _PACKAGE_ROOT = (
        Path(__file__).parents[3]
        / "src"
        / "operations_center"
        / "audit_governance"
    )

    _FORBIDDEN_PREFIXES = (
        "videofoundry",
        "managed_repo",
        "kodo",
        "codex",
        "archon",
    )

    def _collect_imports(self, source: str) -> list[str]:
        tree = ast.parse(source)
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    @pytest.mark.parametrize("py_file", list(_PACKAGE_ROOT.glob("*.py")))
    def test_no_forbidden_imports(self, py_file: Path):
        source = py_file.read_text(encoding="utf-8")
        imports = self._collect_imports(source)
        for imp in imports:
            for prefix in self._FORBIDDEN_PREFIXES:
                assert not imp.startswith(prefix), (
                    f"{py_file.name} imports forbidden module {imp!r}"
                )

    @pytest.mark.parametrize("py_file", list(_PACKAGE_ROOT.glob("*.py")))
    def test_no_fixture_harvesting_import(self, py_file: Path):
        source = py_file.read_text(encoding="utf-8")
        imports = self._collect_imports(source)
        for imp in imports:
            assert "fixture_harvesting" not in imp, (
                f"{py_file.name} imports fixture_harvesting — governance must not harvest"
            )
            assert "slice_replay" not in imp, (
                f"{py_file.name} imports slice_replay — governance must not replay"
            )
            assert "mini_regression" not in imp, (
                f"{py_file.name} imports mini_regression — governance must not run suites"
            )

    def test_no_scheduler_or_watch_loop(self):
        """No scheduling/daemon code in the governance package."""
        forbidden_patterns = ["schedule", "watchdog", "daemon", "cron", "watch_loop"]
        for py_file in self._PACKAGE_ROOT.glob("*.py"):
            source = py_file.read_text(encoding="utf-8").lower()
            for pattern in forbidden_patterns:
                if pattern in source:
                    # Allow in comments and strings that describe non-goals
                    tree = ast.parse(py_file.read_text(encoding="utf-8"))
                    # Check it's not in import names
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                assert pattern not in alias.name.lower(), (
                                    f"{py_file.name} imports scheduler/daemon: {alias.name}"
                                )


# ---------------------------------------------------------------------------
# Contract 12 — Empty known_repos denies all (gap_006)
# ---------------------------------------------------------------------------

class TestEmptyKnownReposDeniesAll:
    def test_empty_known_repos_returns_failed_policy(self):
        req = _make_request()
        results = evaluate_governance_policies(
            req,
            known_repos=[],
            known_audit_types={"videofoundry": ["representative"]},
        )
        by_name = {p.policy_name: p for p in results}
        assert by_name["known_repo_required"].status == "failed"

    def test_empty_known_repos_produces_denied_decision(self):
        req = _make_request()
        results = evaluate_governance_policies(
            req,
            known_repos=[],
            known_audit_types={"videofoundry": ["representative"]},
        )
        decision = make_governance_decision(req, results)
        assert decision.decision == "denied"

    def test_empty_known_repos_does_not_dispatch(self, tmp_path: Path):
        req = _make_request(
            urgency="normal",
            related_suite_report_path="/suite.json",
        )
        cfg = _make_config(known_repos=[], state_dir=tmp_path / "state")
        dispatch_mock = MagicMock()
        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")
        assert not dispatch_mock.called
        assert result.governance_status == "denied"

    def test_empty_known_repos_reason_is_descriptive(self):
        req = _make_request()
        results = evaluate_governance_policies(req, known_repos=[], known_audit_types={})
        by_name = {p.policy_name: p for p in results}
        assert "denied" in by_name["known_repo_required"].reason.lower() or \
               "known_repos" in by_name["known_repo_required"].reason


# ---------------------------------------------------------------------------
# Contract 13 — File locking prevents concurrent write races (gap_001)
# ---------------------------------------------------------------------------

class TestFileLocking:
    def test_budget_concurrent_increments_no_loss(self, tmp_path: Path):
        """Two threads incrementing budget must both register without losing writes."""
        cfg = BudgetConfig(max_runs=20, period_days=7)
        errors: list[Exception] = []

        def do_increment():
            try:
                increment_budget_after_dispatch(tmp_path, "videofoundry", "representative", cfg)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=do_increment) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent increments: {errors}"
        final = load_budget_state(tmp_path, "videofoundry", "representative", cfg)
        assert final.runs_used == 5

    def test_cooldown_concurrent_writes_no_corruption(self, tmp_path: Path):
        """Multiple threads writing cooldown state must not corrupt the file."""
        cfg = CooldownConfig(cooldown_seconds=3600)
        errors: list[Exception] = []

        def do_update():
            try:
                update_cooldown_after_dispatch(tmp_path, "videofoundry", "representative", cfg)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=do_update) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # File must be valid JSON after concurrent writes
        final = load_cooldown_state(tmp_path, "videofoundry", "representative", cfg)
        assert final.last_run_at is not None

    def test_lock_released_after_success(self, tmp_path: Path):
        """Lock file must not remain locked after normal operation."""
        from operations_center.audit_governance.file_locks import locked_state_file
        lock_target = tmp_path / "test.json"
        with locked_state_file(lock_target):
            pass  # lock acquired and released
        # Can acquire again immediately
        with locked_state_file(lock_target):
            pass

    def test_lock_released_after_exception(self, tmp_path: Path):
        """Lock must be released even when the guarded block raises."""
        from operations_center.audit_governance.file_locks import locked_state_file
        lock_target = tmp_path / "test.json"
        try:
            with locked_state_file(lock_target):
                raise ValueError("simulated error")
        except ValueError:
            pass
        # Lock released — can acquire again
        with locked_state_file(lock_target):
            pass


# ---------------------------------------------------------------------------
# Contract 14 — AuditGovernanceReport.dispatched_run_id property (gap_007)
# ---------------------------------------------------------------------------

class TestDispatchedRunId:
    def test_dispatched_run_id_none_when_not_dispatched(self, tmp_path: Path):
        req = _make_request(repo_id="unknown_repo")
        cfg = _make_config(state_dir=tmp_path / "state")
        result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")
        report = load_governance_report(result.report_path)
        assert report.dispatched_run_id is None

    def test_dispatched_run_id_present_after_dispatch(self, tmp_path: Path):
        req = _make_request(urgency="normal", related_suite_report_path="/suite.json")
        cfg = _make_config(state_dir=tmp_path / "state")
        dispatch_mock = MagicMock(return_value=_make_dispatch_result())
        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")
        report = load_governance_report(result.report_path)
        assert report.dispatched_run_id == "test_run_001"


# ---------------------------------------------------------------------------
# Contract 15 — Negative / failure-path tests (gap_007)
# ---------------------------------------------------------------------------

class TestNegativePaths:
    def test_empty_requested_by_is_denied(self, tmp_path: Path):
        """A request with an empty requester cannot be approved."""
        # empty requested_by is caught at model validation, not policy
        with pytest.raises(Exception):
            AuditGovernanceRequest(
                repo_id="videofoundry",
                audit_type="representative",
                requested_by="",
                requested_reason="some reason",
            )

    def test_wrong_request_id_in_approval_does_not_dispatch(self, tmp_path: Path):
        req = _make_request(urgency="urgent", related_suite_report_path="/suite.json")
        cfg = _make_config(state_dir=tmp_path / "state")
        results = evaluate_governance_policies(
            req, known_repos=cfg.known_repos, known_audit_types=cfg.known_audit_types
        )
        decision = make_governance_decision(req, results)
        # Tamper with request_id
        wrong_approval = AuditManualApproval(
            decision_id=decision.decision_id,
            request_id="wrong_request_id",
            approved_by="bob",
        )
        dispatch_mock = MagicMock(return_value=_make_dispatch_result())
        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(
                req, approval=wrong_approval, governance_config=cfg, output_dir=tmp_path / "out"
            )
        assert not dispatch_mock.called
        assert result.governance_status in ("denied", "needs_manual_approval")

    def test_corrupt_budget_state_file_raises(self, tmp_path: Path):
        corrupt_path = tmp_path / "videofoundry__representative__budget.json"
        corrupt_path.write_text("NOT JSON {{{", encoding="utf-8")
        from operations_center.audit_governance.errors import BudgetStateError
        cfg = BudgetConfig(max_runs=5, period_days=7)
        with pytest.raises(BudgetStateError, match="Cannot load budget state"):
            load_budget_state(tmp_path, "videofoundry", "representative", cfg)

    def test_corrupt_cooldown_state_file_raises(self, tmp_path: Path):
        corrupt_path = tmp_path / "videofoundry__representative__cooldown.json"
        corrupt_path.write_text("NOT JSON {{{", encoding="utf-8")
        from operations_center.audit_governance.errors import CooldownStateError
        cfg = CooldownConfig(cooldown_seconds=3600)
        with pytest.raises(CooldownStateError, match="Cannot load cooldown state"):
            load_cooldown_state(tmp_path, "videofoundry", "representative", cfg)

    def test_budget_exhausted_blocks_dispatch(self, tmp_path: Path):
        cfg = _make_config(
            state_dir=tmp_path / "state",
            budget_config={"videofoundry": {"representative": BudgetConfig(max_runs=1, period_days=7)}},
        )
        # Exhaust budget
        increment_budget_after_dispatch(
            tmp_path / "state", "videofoundry", "representative", BudgetConfig(max_runs=1, period_days=7)
        )
        req = _make_request(urgency="normal", related_suite_report_path="/suite.json")
        dispatch_mock = MagicMock()
        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")
        assert not dispatch_mock.called
        assert result.governance_status == "deferred"

    def test_governance_report_load_invalid_schema_raises(self, tmp_path: Path):
        bad = tmp_path / "report.json"
        bad.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
        with pytest.raises(GovernanceReportError):
            load_governance_report(bad)
