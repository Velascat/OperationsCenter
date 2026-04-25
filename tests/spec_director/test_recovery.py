# tests/spec_director/test_recovery.py
from __future__ import annotations
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from operations_center.spec_director.models import CampaignRecord


def _campaign(hours_ago: int = 30) -> CampaignRecord:
    past = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    return CampaignRecord(
        campaign_id="abc", slug="add-auth", spec_file="docs/specs/add-auth.md",
        status="active",
        created_at=past,
    )


def test_abandon_threshold_exceeded():
    from operations_center.spec_director.recovery import RecoveryService
    campaign = _campaign(hours_ago=80)
    service = RecoveryService(
        client=MagicMock(), state_manager=MagicMock(),
        abandon_hours=72,
    )
    assert service.should_abandon(campaign) is True


def test_no_abandon_when_recent():
    from operations_center.spec_director.recovery import RecoveryService
    campaign = _campaign(hours_ago=1)
    service = RecoveryService(
        client=MagicMock(), state_manager=MagicMock(),
        abandon_hours=72,
    )
    assert service.should_abandon(campaign) is False


def test_self_cancel_marks_cancelled(tmp_path):
    from operations_center.spec_director.recovery import RecoveryService

    spec_file = tmp_path / "add-auth.md"
    spec_file.write_text("---\nstatus: active\n---\n# Spec\n")

    state_mock = MagicMock()
    client_mock = MagicMock()
    client_mock.list_issues.return_value = []

    service = RecoveryService(client=client_mock, state_manager=state_mock)
    campaign = _campaign(hours_ago=80)
    service.self_cancel(campaign, "abandon_hours_exceeded", tmp_path)

    state_mock.mark_cancelled.assert_called_once_with("abc")
    assert "status: cancelled" in spec_file.read_text()
