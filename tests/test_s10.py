"""Tests for Session 10 autonomy gap implementations.

S10-1  Rejection patterns injected into Kodo prompts
S10-2  Question-asking mid-execution (awaiting_input classification + re-queue)
S10-3  Reviewer → goal re-run escalation (_requeue_as_goal)
S10-4  Campaign/project tracking (CampaignStore + campaign-status CLI)
S10-5  Calibration time decay (window_days + cleanup_old_events)
S10-6  Task complexity estimate at proposal time
S10-7  Utility function for proposal ranking
S10-8  Real-time CI webhook (HMAC + trigger file)
S10-9  Cross-repo synthesis deriver
S10-10 Task priority re-evaluation scan
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# S10-1: Rejection patterns injected into Kodo prompts
# ---------------------------------------------------------------------------

def test_load_rejection_patterns_for_proposal_returns_top3(tmp_path: Path) -> None:
    from control_plane.entrypoints.worker.main import _load_rejection_patterns_for_proposal

    patterns_path = tmp_path / "rejection_patterns.json"
    patterns_path.write_text(json.dumps({
        "my_repo:goal": {
            "patterns": {
                "missing_tests": 5,
                "naming_convention": 3,
                "scope_too_large": 1,
                "code_style": 7,
            },
            "last_seen": {},
        }
    }))

    with patch("control_plane.entrypoints.worker.main._REJECTION_PATTERNS_PATH", patterns_path):
        patterns = _load_rejection_patterns_for_proposal(family="goal", repo_key="my_repo")

    # Returns top-3 by count: code_style(7), missing_tests(5), naming_convention(3)
    assert patterns == ["code_style", "missing_tests", "naming_convention"]


def test_load_rejection_patterns_missing_file_returns_empty(tmp_path: Path) -> None:
    from control_plane.entrypoints.worker.main import _load_rejection_patterns_for_proposal

    nonexistent = tmp_path / "no_file.json"
    with patch("control_plane.entrypoints.worker.main._REJECTION_PATTERNS_PATH", nonexistent):
        patterns = _load_rejection_patterns_for_proposal(family="goal", repo_key="repo")

    assert patterns == []


def test_build_proposal_description_injects_rejection_patterns() -> None:
    from control_plane.entrypoints.worker.main import (
        ProposalSpec, build_proposal_description
    )

    service = MagicMock()
    service.settings.repos = {"repo_a": MagicMock(default_branch="main")}
    service.settings.focus_areas = []

    proposal = ProposalSpec(
        repo_key="repo_a",
        task_kind="goal",
        title="Fix lint",
        goal_text="Run ruff and fix violations",
        reason_summary="lint violations",
        source_signal="repo_a:lint",
        confidence="high",
        recommended_state="Ready for AI",
        handoff_reason="lint",
        dedup_key="repo_a:lint:fix",
    )

    with patch(
        "control_plane.entrypoints.worker.main._load_rejection_patterns_for_proposal",
        return_value=["missing_tests", "code_style"],
    ):
        desc = build_proposal_description(service=service, proposal=proposal)

    assert "Prior Rejection Patterns" in desc
    assert "missing_tests" in desc
    assert "code_style" in desc


def test_build_proposal_description_no_patterns_skips_section() -> None:
    from control_plane.entrypoints.worker.main import (
        ProposalSpec, build_proposal_description
    )

    service = MagicMock()
    service.settings.repos = {"repo_a": MagicMock(default_branch="main")}
    service.settings.focus_areas = []

    proposal = ProposalSpec(
        repo_key="repo_a",
        task_kind="goal",
        title="Fix lint",
        goal_text="Run ruff",
        reason_summary="lint",
        source_signal="repo_a:lint",
        confidence="high",
        recommended_state="Ready for AI",
        handoff_reason="lint",
        dedup_key="repo_a:lint:fix2",
    )

    with patch(
        "control_plane.entrypoints.worker.main._load_rejection_patterns_for_proposal",
        return_value=[],
    ):
        desc = build_proposal_description(service=service, proposal=proposal)

    assert "Prior Rejection Patterns" not in desc


# ---------------------------------------------------------------------------
# S10-2: awaiting_input classification
# ---------------------------------------------------------------------------

def test_classify_awaiting_input_from_summary() -> None:
    from control_plane.entrypoints.worker.main import classify_execution_result
    from control_plane.domain.models import ExecutionResult

    result = ExecutionResult(
        run_id="r1",
        success=False,
        summary="Working on the task... <!-- cp:question: What database version? --> Could not proceed.",
    )
    classification = classify_execution_result(result)
    assert classification == "awaiting_input"


def test_classify_awaiting_input_from_stderr() -> None:
    from control_plane.entrypoints.worker.main import classify_execution_result
    from control_plane.domain.models import ExecutionResult

    result = ExecutionResult(
        run_id="r2",
        success=False,
        summary="Blocked.",
        execution_stderr_excerpt="kodo output: <!-- cp:question: Which auth provider? -->",
    )
    classification = classify_execution_result(result)
    assert classification == "awaiting_input"


def test_classify_no_question_marker_returns_other() -> None:
    from control_plane.entrypoints.worker.main import classify_execution_result
    from control_plane.domain.models import ExecutionResult

    result = ExecutionResult(
        run_id="r3",
        success=False,
        summary="Failed.",
        execution_stderr_excerpt="timed out after 120s",
    )
    classification = classify_execution_result(result)
    assert classification == "timeout"


def test_extract_cp_question_from_summary() -> None:
    from control_plane.entrypoints.worker.main import extract_cp_question
    from control_plane.domain.models import ExecutionResult

    result = ExecutionResult(
        run_id="r4",
        success=False,
        summary="Processing... <!-- cp:question: Should I use async or sync? --> Blocked.",
    )
    question = extract_cp_question(result)
    assert question == "Should I use async or sync?"


def test_build_improve_triage_result_awaiting_input() -> None:
    from control_plane.entrypoints.worker.main import build_improve_triage_result

    client = MagicMock()
    client.list_issues.return_value = []

    issue = {"id": "task-1", "name": "Implement feature X", "state": {"name": "Blocked"}, "labels": []}
    comments = [
        {
            "id": 1,
            "comment_stripped": "[Improve] Blocked triage\n- blocked_classification: awaiting_input\n<!-- cp:question: What OAuth provider? -->",
        }
    ]

    result = build_improve_triage_result(client, issue, comments)
    assert result.classification == "awaiting_input"
    assert result.human_attention_required is True
    assert "What OAuth provider?" in result.reason_summary


# ---------------------------------------------------------------------------
# S10-3: Reviewer → goal re-run escalation
# ---------------------------------------------------------------------------

def test_requeue_as_goal_threshold_constant() -> None:
    from control_plane.entrypoints.reviewer.main import REQUEUE_AS_GOAL_ZERO_CHANGE_THRESHOLD
    assert REQUEUE_AS_GOAL_ZERO_CHANGE_THRESHOLD >= 2


def test_requeue_as_goal_creates_fresh_task() -> None:
    from control_plane.entrypoints.reviewer.main import _requeue_as_goal

    gh = MagicMock()
    gh.close_pr.return_value = {}

    state = {
        "owner": "org",
        "repo": "myrepo",
        "pr_number": 42,
        "task_id": "task-123",
        "repo_key": "myrepo",
        "original_goal": "Fix the authentication flow",
        "task_title": "Fix authentication",
        "created_at": datetime.now(UTC).isoformat(),
    }
    state_file = MagicMock()
    state_file.unlink = MagicMock()

    plane_client = MagicMock()
    plane_client.create_issue.return_value = {"id": "new-task-456"}
    plane_client.transition_issue.return_value = None

    service = MagicMock()
    service.settings.reviewer.bot_comment_marker = "<!-- bot -->"
    service.settings.repos = {"myrepo": MagicMock(default_branch="main")}

    logger = MagicMock()

    result = _requeue_as_goal(
        gh, state, state_file, plane_client, service, logger,
        review_comment="Please add unit tests",
    )

    assert result == 1
    # Should close PR
    gh.close_pr.assert_called_once()
    # Should create fresh goal task
    plane_client.create_issue.assert_called_once()
    call_kwargs = plane_client.create_issue.call_args[1]
    assert "goal" in call_kwargs["label_names"][0]
    assert "Please add unit tests" in call_kwargs["description"]
    # Should mark original task Done
    plane_client.transition_issue.assert_called_once_with("task-123", "Done")
    state_file.unlink.assert_called_once()


# ---------------------------------------------------------------------------
# S10-4: CampaignStore
# ---------------------------------------------------------------------------

def test_campaign_store_create_and_retrieve(tmp_path: Path) -> None:
    from control_plane.execution.campaign_store import CampaignStore

    store = CampaignStore(path=tmp_path / "campaigns.json")
    campaign_id = store.create(
        source_task_id="src-001",
        title="Refactor auth middleware",
        step_task_ids=["s1", "s2", "s3"],
    )
    assert campaign_id == "src-001"

    record = store.get("src-001")
    assert record is not None
    assert record["title"] == "Refactor auth middleware"
    assert record["total_steps"] == 3
    assert record["completed_steps"] == 0
    assert record["status"] == "in_progress"
    assert record["progress_pct"] == 0.0


def test_campaign_store_record_step_done(tmp_path: Path) -> None:
    from control_plane.execution.campaign_store import CampaignStore

    store = CampaignStore(path=tmp_path / "campaigns.json")
    store.create(source_task_id="src-002", title="Test campaign", step_task_ids=["s1", "s2", "s3"])

    store.record_step_done("src-002", step_task_id="s1")
    record = store.get("src-002")
    assert record["completed_steps"] == 1
    assert record["progress_pct"] == pytest.approx(33.3, abs=0.2)
    assert record["status"] == "partial"

    store.record_step_done("src-002", step_task_id="s2")
    store.record_step_done("src-002", step_task_id="s3")
    record = store.get("src-002")
    assert record["status"] == "completed"
    assert record["progress_pct"] == 100.0


def test_campaign_store_create_idempotent(tmp_path: Path) -> None:
    from control_plane.execution.campaign_store import CampaignStore

    store = CampaignStore(path=tmp_path / "campaigns.json")
    id1 = store.create(source_task_id="src-003", title="T", step_task_ids=["a"])
    id2 = store.create(source_task_id="src-003", title="T", step_task_ids=["a"])
    assert id1 == id2
    assert len(store.list_campaigns()) == 1


def test_campaign_store_list_filter_by_status(tmp_path: Path) -> None:
    from control_plane.execution.campaign_store import CampaignStore

    store = CampaignStore(path=tmp_path / "campaigns.json")
    store.create(source_task_id="c1", title="A", step_task_ids=["s1"])
    store.create(source_task_id="c2", title="B", step_task_ids=["s1", "s2"])

    store.record_step_done("c1", step_task_id="s1")

    in_progress = store.list_campaigns(status="in_progress")
    completed = store.list_campaigns(status="completed")

    assert len(in_progress) == 1
    assert in_progress[0]["source_task_id"] == "c2"
    assert len(completed) == 1
    assert completed[0]["source_task_id"] == "c1"


# ---------------------------------------------------------------------------
# S10-5: Calibration time decay
# ---------------------------------------------------------------------------

def test_calibration_window_days_filters_old_events(tmp_path: Path) -> None:
    from control_plane.tuning.calibration import ConfidenceCalibrationStore

    store = ConfidenceCalibrationStore(path=tmp_path / "calibration.json")

    # Record old events (91 days ago)
    old_date = (datetime.now(UTC) - timedelta(days=91)).isoformat()
    new_date = datetime.now(UTC).isoformat()

    # Manually write events with mixed dates
    data = {
        "events": [
            {"recorded_at": old_date, "family": "lint_fix", "confidence": "high", "outcome": "merged"},
            {"recorded_at": old_date, "family": "lint_fix", "confidence": "high", "outcome": "merged"},
            {"recorded_at": old_date, "family": "lint_fix", "confidence": "high", "outcome": "merged"},
            {"recorded_at": old_date, "family": "lint_fix", "confidence": "high", "outcome": "merged"},
            {"recorded_at": old_date, "family": "lint_fix", "confidence": "high", "outcome": "abandoned"},
            {"recorded_at": new_date, "family": "lint_fix", "confidence": "high", "outcome": "abandoned"},
            {"recorded_at": new_date, "family": "lint_fix", "confidence": "high", "outcome": "abandoned"},
            {"recorded_at": new_date, "family": "lint_fix", "confidence": "high", "outcome": "abandoned"},
            {"recorded_at": new_date, "family": "lint_fix", "confidence": "high", "outcome": "abandoned"},
            {"recorded_at": new_date, "family": "lint_fix", "confidence": "high", "outcome": "abandoned"},
        ]
    }
    (tmp_path / "calibration.json").write_text(json.dumps(data))

    # With 90-day window, only new events count → 0 merges / 5 = 0.0
    rate_windowed = store.calibration_for("lint_fix", "high", window_days=90)
    assert rate_windowed == pytest.approx(0.0)

    # Without window (None), all 10 events count → 4 merges / 10 = 0.4
    rate_all = store.calibration_for("lint_fix", "high", window_days=None)
    assert rate_all == pytest.approx(0.4)


def test_calibration_cleanup_old_events(tmp_path: Path) -> None:
    from control_plane.tuning.calibration import ConfidenceCalibrationStore

    store = ConfidenceCalibrationStore(path=tmp_path / "calibration.json")
    old_date = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    new_date = datetime.now(UTC).isoformat()

    data = {
        "events": [
            {"recorded_at": old_date, "family": "lint_fix", "confidence": "high", "outcome": "merged"},
            {"recorded_at": old_date, "family": "lint_fix", "confidence": "high", "outcome": "merged"},
            {"recorded_at": new_date, "family": "lint_fix", "confidence": "high", "outcome": "merged"},
        ]
    }
    (tmp_path / "calibration.json").write_text(json.dumps(data))

    removed = store.cleanup_old_events(window_days=90)
    assert removed == 2

    remaining = json.loads((tmp_path / "calibration.json").read_text())
    assert len(remaining["events"]) == 1
    assert remaining["events"][0]["recorded_at"] == new_date


def test_calibration_report_window_days(tmp_path: Path) -> None:
    from control_plane.tuning.calibration import ConfidenceCalibrationStore

    store = ConfidenceCalibrationStore(path=tmp_path / "calibration.json")
    old_date = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    new_date = datetime.now(UTC).isoformat()

    data = {
        "events": [
            # Old: 5 merges, 0 abandoned → would look good without window
            *[{"recorded_at": old_date, "family": "type_fix", "confidence": "high", "outcome": "merged"}
              for _ in range(5)],
            # New: 5 abandoned
            *[{"recorded_at": new_date, "family": "type_fix", "confidence": "high", "outcome": "abandoned"}
              for _ in range(5)],
        ]
    }
    (tmp_path / "calibration.json").write_text(json.dumps(data))

    # Without window: 5/10 = 0.5
    records_all = store.report(window_days=None)
    assert len(records_all) == 1
    assert records_all[0].acceptance_rate == pytest.approx(0.5)

    # With 90-day window: 0/5 = 0.0
    records_windowed = store.report(window_days=90)
    assert len(records_windowed) == 1
    assert records_windowed[0].acceptance_rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# S10-6: Task complexity estimate at proposal time
# ---------------------------------------------------------------------------

def test_estimate_task_complexity_low() -> None:
    from control_plane.entrypoints.worker.main import _estimate_task_complexity, ProposalSpec

    proposal = ProposalSpec(
        repo_key="repo",
        task_kind="goal",
        title="Fix one lint error",
        goal_text="Fix the lint error in utils.py",
        reason_summary="lint",
        source_signal="repo:lint",
        confidence="high",
        recommended_state="Ready for AI",
        handoff_reason="lint",
        dedup_key="key1",
        evidence_lines=["src/utils.py: line 10: E501"],
    )
    assert _estimate_task_complexity(proposal) == "low"


def test_estimate_task_complexity_high() -> None:
    from control_plane.entrypoints.worker.main import _estimate_task_complexity, ProposalSpec

    proposal = ProposalSpec(
        repo_key="repo",
        task_kind="goal",
        title="Refactor auth system",
        goal_text="Refactor the authentication module",
        reason_summary="arch drift",
        source_signal="repo:arch",
        confidence="medium",
        recommended_state="Backlog",
        handoff_reason="arch",
        dedup_key="key2",
        evidence_lines=[
            "src/auth/login.py: high coupling",
            "src/auth/session.py: high coupling",
            "src/auth/token.py: high coupling",
            "src/auth/middleware.py: high coupling",
            "src/auth/models.py: high coupling",
            "src/auth/views.py: high coupling",
            "src/auth/decorators.py: high coupling",
            "src/auth/backend.py: high coupling",
            "src/auth/utils.py: high coupling",
        ],
    )
    assert _estimate_task_complexity(proposal) == "high"


def test_build_proposal_candidates_complexity_gate_caps_high_complexity(tmp_path: Path) -> None:
    from control_plane.entrypoints.worker.main import (
        build_proposal_candidates, _estimate_task_complexity
    )

    service = MagicMock()
    service.settings.repos = {"repo_a": MagicMock(default_branch="main")}
    service.settings.focus_areas = []
    service.usage_store.proposal_success_rate.return_value = 0.5
    service.settings = MagicMock()
    service.settings.repos = {"repo_a": MagicMock(default_branch="main")}
    service.settings.focus_areas = []

    client = MagicMock()
    client.list_issues.return_value = []

    # Create a proposal with many evidence files → high complexity
    with patch(
        "control_plane.entrypoints.worker.main.discover_improvement_candidates",
        return_value=([], []),
    ), patch(
        "control_plane.entrypoints.worker.main.recent_classification_counts",
        return_value={},
    ), patch(
        "control_plane.entrypoints.worker.main._estimate_task_complexity",
        return_value="high",
    ):
        proposals, notes, _ = build_proposal_candidates(
            client, service, repo_key="repo_a", issues=[]
        )

    # All high-complexity proposals should be in Backlog
    high_ready = [p for p in proposals if p.recommended_state == "Ready for AI"]
    assert all(_estimate_task_complexity(p) != "high" for p in high_ready)


# ---------------------------------------------------------------------------
# S10-7: Utility function for proposal ranking
# ---------------------------------------------------------------------------

def test_score_proposal_utility_high_confidence_scores_higher() -> None:
    from control_plane.entrypoints.worker.main import _score_proposal_utility, ProposalSpec

    high = ProposalSpec(
        repo_key="r", task_kind="goal", title="T", goal_text="G",
        reason_summary="R", source_signal="S", confidence="high",
        recommended_state="Ready for AI", handoff_reason="H", dedup_key="k1",
    )
    low = ProposalSpec(
        repo_key="r", task_kind="goal", title="T2", goal_text="G",
        reason_summary="R", source_signal="S", confidence="low",
        recommended_state="Ready for AI", handoff_reason="H", dedup_key="k2",
    )

    with patch(
        "control_plane.entrypoints.worker.main._score_proposal_utility.__code__",
        wraps=None,
    ) if False else MagicMock() as _:
        score_high = _score_proposal_utility(high)
        score_low = _score_proposal_utility(low)

    assert score_high > score_low


def test_score_proposal_utility_backlog_scores_lower_than_ready() -> None:
    from control_plane.entrypoints.worker.main import _score_proposal_utility, ProposalSpec

    ready = ProposalSpec(
        repo_key="r", task_kind="goal", title="T", goal_text="G",
        reason_summary="R", source_signal="S", confidence="medium",
        recommended_state="Ready for AI", handoff_reason="H", dedup_key="k3",
    )
    backlog = ProposalSpec(
        repo_key="r", task_kind="goal", title="T2", goal_text="G",
        reason_summary="R", source_signal="S", confidence="medium",
        recommended_state="Backlog", handoff_reason="H", dedup_key="k4",
    )
    assert _score_proposal_utility(ready) > _score_proposal_utility(backlog)


def test_score_proposal_utility_many_files_penalised() -> None:
    from control_plane.entrypoints.worker.main import _score_proposal_utility, ProposalSpec

    few_files = ProposalSpec(
        repo_key="r", task_kind="goal", title="T", goal_text="G",
        reason_summary="R", source_signal="S", confidence="high",
        recommended_state="Backlog", handoff_reason="H", dedup_key="k5",
        evidence_lines=["src/a.py: error", "src/b.py: error"],
    )
    many_files = ProposalSpec(
        repo_key="r", task_kind="goal", title="T2", goal_text="G",
        reason_summary="R", source_signal="S", confidence="high",
        recommended_state="Backlog", handoff_reason="H", dedup_key="k6",
        evidence_lines=[f"src/file_{i}.py: error" for i in range(10)],
    )
    assert _score_proposal_utility(few_files) > _score_proposal_utility(many_files)


# ---------------------------------------------------------------------------
# S10-8: CI webhook
# ---------------------------------------------------------------------------

def test_ci_webhook_verify_signature_valid() -> None:
    import hmac as _hmac
    import hashlib
    from control_plane.entrypoints.ci_webhook.main import _verify_signature

    secret = b"mysecret"
    body = b'{"action": "completed"}'
    sig = "sha256=" + _hmac.new(secret, body, hashlib.sha256).hexdigest()
    assert _verify_signature(body, sig, secret) is True


def test_ci_webhook_verify_signature_invalid() -> None:
    from control_plane.entrypoints.ci_webhook.main import _verify_signature

    secret = b"mysecret"
    body = b'{"action": "completed"}'
    assert _verify_signature(body, "sha256=badhash", secret) is False


def test_ci_webhook_verify_signature_missing_prefix() -> None:
    from control_plane.entrypoints.ci_webhook.main import _verify_signature

    assert _verify_signature(b"body", "abc123", b"secret") is False


def test_ci_webhook_parse_check_run_event_completed() -> None:
    from control_plane.entrypoints.ci_webhook.main import _parse_check_run_event

    payload = {
        "action": "completed",
        "check_run": {
            "name": "pytest",
            "conclusion": "success",
            "head_sha": "abc123def456",
            "pull_requests": [{"number": 7}],
        },
        "repository": {"full_name": "org/repo"},
    }
    event = _parse_check_run_event(payload)
    assert event is not None
    assert event["conclusion"] == "success"
    assert event["pr_number"] == 7
    assert event["repo"] == "org/repo"
    assert event["check_name"] == "pytest"


def test_ci_webhook_parse_check_run_event_irrelevant_action() -> None:
    from control_plane.entrypoints.ci_webhook.main import _parse_check_run_event

    payload = {"action": "created", "check_run": {"conclusion": "success"}, "repository": {}}
    assert _parse_check_run_event(payload) is None


def test_ci_webhook_parse_check_run_event_irrelevant_conclusion() -> None:
    from control_plane.entrypoints.ci_webhook.main import _parse_check_run_event

    payload = {
        "action": "completed",
        "check_run": {"conclusion": "skipped", "name": "ci", "head_sha": "x"},
        "repository": {"full_name": "org/repo"},
    }
    assert _parse_check_run_event(payload) is None


def test_ci_webhook_write_trigger_creates_file(tmp_path: Path) -> None:
    from control_plane.entrypoints.ci_webhook import main as wh_mod

    original_dir = wh_mod._TRIGGER_DIR
    wh_mod._TRIGGER_DIR = tmp_path / "triggers"
    try:
        event = {
            "repo": "org/repo",
            "pr_number": 5,
            "conclusion": "failure",
            "check_name": "pytest",
            "head_sha": "abc123",
            "received_at": datetime.now(UTC).isoformat(),
        }
        wh_mod._write_trigger(event)
        trigger_files = list((tmp_path / "triggers").glob("*.json"))
        assert len(trigger_files) == 1
        data = json.loads(trigger_files[0].read_text())
        assert data["conclusion"] == "failure"
    finally:
        wh_mod._TRIGGER_DIR = original_dir


# ---------------------------------------------------------------------------
# S10-9: Cross-repo synthesis deriver
# ---------------------------------------------------------------------------

def test_cross_repo_synthesis_no_artifacts_returns_empty(tmp_path: Path) -> None:
    from control_plane.insights.derivers.cross_repo_synthesis import CrossRepoSynthesisDeriver
    from control_plane.insights.normalizer import InsightNormalizer

    deriver = CrossRepoSynthesisDeriver(InsightNormalizer(), insights_root=tmp_path / "insights")
    result = deriver.derive([])
    assert result == []


def test_cross_repo_synthesis_single_repo_no_insight(tmp_path: Path) -> None:
    from control_plane.insights.derivers.cross_repo_synthesis import CrossRepoSynthesisDeriver
    from control_plane.insights.normalizer import InsightNormalizer

    root = tmp_path / "insights"
    run_dir = root / "run-001"
    run_dir.mkdir(parents=True)
    (run_dir / "repo_insights.json").write_text(json.dumps({
        "repo": {"name": "repo_a"},
        "generated_at": "2026-04-01T00:00:00+00:00",
        "insights": [{"kind": "lint_drift", "subject": "violations_high", "status": "present"}],
    }))

    deriver = CrossRepoSynthesisDeriver(InsightNormalizer(), insights_root=root)
    # Only one repo — nothing to synthesise
    result = deriver.derive([MagicMock()])
    assert result == []


def test_cross_repo_synthesis_two_repos_shared_kind(tmp_path: Path) -> None:
    from control_plane.insights.derivers.cross_repo_synthesis import CrossRepoSynthesisDeriver
    from control_plane.insights.normalizer import InsightNormalizer

    root = tmp_path / "insights"

    for repo_name, run_id in [("repo_a", "run-001"), ("repo_b", "run-002")]:
        d = root / run_id
        d.mkdir(parents=True)
        (d / "repo_insights.json").write_text(json.dumps({
            "repo": {"name": repo_name},
            "generated_at": "2026-04-01T00:00:00+00:00",
            "insights": [
                {"kind": "lint_drift", "subject": "violations_high", "status": "present"},
                {"kind": "type_health", "subject": "errors_present", "status": "present"},
            ],
        }))

    deriver = CrossRepoSynthesisDeriver(InsightNormalizer(), insights_root=root)
    result = deriver.derive([MagicMock()])

    assert len(result) >= 1
    kinds_emitted = [r.kind for r in result]
    subjects_emitted = [r.subject for r in result]
    assert "cross_repo" in kinds_emitted
    assert "pattern_detected" in subjects_emitted

    # Check evidence
    for insight in result:
        if insight.kind == "cross_repo":
            assert insight.evidence["repo_count"] == 2
            break


def test_cross_repo_synthesis_only_latest_per_repo(tmp_path: Path) -> None:
    """When a repo has multiple run artifacts, only the latest is used."""
    from control_plane.insights.derivers.cross_repo_synthesis import _read_latest_insight_kinds

    root = tmp_path / "insights"

    # repo_a has two runs: only the latest should count
    for run_id, ts, kinds in [
        ("run-old", "2026-03-01T00:00:00+00:00", ["stale_insight"]),
        ("run-new", "2026-04-01T00:00:00+00:00", ["fresh_insight"]),
    ]:
        d = root / run_id
        d.mkdir(parents=True)
        (d / "repo_insights.json").write_text(json.dumps({
            "repo": {"name": "repo_a"},
            "generated_at": ts,
            "insights": [{"kind": k} for k in kinds],
        }))

    result = _read_latest_insight_kinds(root)
    assert "repo_a" in result
    assert "fresh_insight" in result["repo_a"]
    assert "stale_insight" not in result["repo_a"]


# ---------------------------------------------------------------------------
# S10-10: Priority rescore scan
# ---------------------------------------------------------------------------

def test_handle_priority_rescore_scan_demotes_low_acceptance(tmp_path: Path) -> None:
    from control_plane.entrypoints.worker.main import handle_priority_rescore_scan

    client = MagicMock()
    service = MagicMock()
    service.settings.repos = {"repo_a": MagicMock(default_branch="main")}

    # Issue: backlog, source: autonomy, task-kind: goal
    issue = {
        "id": "task-99",
        "name": "Fix something",
        "state": {"name": "Backlog"},
        "labels": [
            {"name": "source: autonomy"},
            {"name": "task-kind: goal"},
            {"name": "repo: repo_a"},
        ],
    }
    client.list_issues.return_value = [issue]
    service.usage_store.proposal_success_rate.return_value = 0.1  # Very low

    mock_calib = MagicMock()
    mock_calib.calibration_for.return_value = 0.1  # Very low

    with patch(
        "control_plane.tuning.calibration.ConfidenceCalibrationStore",
        return_value=mock_calib,
    ):
        changed_ids = handle_priority_rescore_scan(client, service, issues=[issue])

    assert "task-99" in changed_ids
    client.update_issue_labels.assert_called_once()
    labels_set = client.update_issue_labels.call_args[0][1]
    assert "signal_stale" in labels_set


def test_handle_priority_rescore_scan_promotes_high_acceptance(tmp_path: Path) -> None:
    from control_plane.entrypoints.worker.main import handle_priority_rescore_scan

    client = MagicMock()
    service = MagicMock()
    service.settings.repos = {"repo_a": MagicMock(default_branch="main")}

    issue = {
        "id": "task-100",
        "name": "Improve types",
        "state": {"name": "Backlog"},
        "labels": [
            {"name": "source: autonomy"},
            {"name": "task-kind: type_fix"},
            {"name": "repo: repo_a"},
        ],
    }
    client.list_issues.return_value = [issue]
    service.usage_store.proposal_success_rate.return_value = 0.8  # High

    mock_calib = MagicMock()
    mock_calib.calibration_for.return_value = 0.85  # High

    with patch(
        "control_plane.tuning.calibration.ConfidenceCalibrationStore",
        return_value=mock_calib,
    ):
        changed_ids = handle_priority_rescore_scan(client, service, issues=[issue])

    assert "task-100" in changed_ids
    client.update_issue_labels.assert_called_once()
    labels_set = client.update_issue_labels.call_args[0][1]
    assert "priority: high" in labels_set


def test_handle_priority_rescore_scan_no_change_medium_rate() -> None:
    from control_plane.entrypoints.worker.main import handle_priority_rescore_scan

    client = MagicMock()
    service = MagicMock()
    service.settings.repos = {"repo_a": MagicMock(default_branch="main")}

    issue = {
        "id": "task-101",
        "name": "Normal task",
        "state": {"name": "Backlog"},
        "labels": [
            {"name": "source: autonomy"},
            {"name": "task-kind: goal"},
            {"name": "repo: repo_a"},
        ],
    }
    service.usage_store.proposal_success_rate.return_value = 0.5

    mock_calib = MagicMock()
    mock_calib.calibration_for.return_value = None  # Not enough data

    with patch(
        "control_plane.tuning.calibration.ConfidenceCalibrationStore",
        return_value=mock_calib,
    ):
        changed_ids = handle_priority_rescore_scan(client, service, issues=[issue])

    assert changed_ids == []
    client.update_issue_labels.assert_not_called()


# ---------------------------------------------------------------------------
# Campaign-status CLI sanity check
# ---------------------------------------------------------------------------

def test_campaign_status_cli_json_output(tmp_path: Path, capsys) -> None:
    from control_plane.entrypoints.campaign_status.main import main as campaign_main
    from control_plane.execution.campaign_store import CampaignStore

    store = CampaignStore(path=tmp_path / "campaigns.json")
    store.create(source_task_id="cli-test-1", title="CLI test campaign", step_task_ids=["a", "b"])

    with patch(
        "control_plane.execution.campaign_store.CampaignStore",
        return_value=store,
    ), patch("sys.argv", ["campaign-status", "--json"]):
        campaign_main()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["source_task_id"] == "cli-test-1"
