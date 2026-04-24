"""Tests for Session 10 autonomy gap implementations.

S10-4  Campaign/project tracking (CampaignStore + campaign-status CLI)
S10-5  Calibration time decay (window_days + cleanup_old_events)
S10-8  Real-time CI webhook (HMAC + trigger file)
S10-9  Cross-repo synthesis deriver
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest



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
