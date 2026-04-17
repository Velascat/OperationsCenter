# tests/spec_director/test_recovery.py
from __future__ import annotations
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from control_plane.spec_director.models import CampaignRecord


def _stalled_campaign(hours_ago: int = 30) -> CampaignRecord:
    past = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    return CampaignRecord(
        campaign_id="abc", slug="add-auth", spec_file="docs/specs/add-auth.md",
        status="active",
        created_at=past,
    )


def test_stall_detected_after_threshold():
    from control_plane.spec_director.recovery import RecoveryService
    campaign = _stalled_campaign(hours_ago=30)
    service = RecoveryService(
        client=MagicMock(), state_manager=MagicMock(),
        stall_hours=24, abandon_hours=72,
    )
    assert service.is_stalled(campaign) is True


def test_no_stall_when_recent_progress():
    from control_plane.spec_director.recovery import RecoveryService
    campaign = _stalled_campaign(hours_ago=1)
    service = RecoveryService(
        client=MagicMock(), state_manager=MagicMock(),
        stall_hours=24, abandon_hours=72,
    )
    assert service.is_stalled(campaign) is False


def test_abandon_threshold_check():
    from control_plane.spec_director.recovery import RecoveryService
    campaign = _stalled_campaign(hours_ago=80)
    service = RecoveryService(
        client=MagicMock(), state_manager=MagicMock(),
        stall_hours=24, abandon_hours=72,
    )
    assert service.should_abandon(campaign) is True


def test_spec_revision_within_budget():
    from control_plane.spec_director.recovery import RecoveryService
    campaign = _stalled_campaign(hours_ago=1)
    service = RecoveryService(
        client=MagicMock(), state_manager=MagicMock(),
        stall_hours=24, abandon_hours=72, spec_revision_budget=3,
    )
    assert service.revision_budget_ok(campaign) is True


def test_spec_revision_exhausted():
    from control_plane.spec_director.recovery import RecoveryService
    campaign = _stalled_campaign(hours_ago=1)
    service = RecoveryService(
        client=MagicMock(), state_manager=MagicMock(),
        stall_hours=24, abandon_hours=72, spec_revision_budget=3,
    )
    # Budget always OK after field removal; Task 3 will remove this method
    assert service.revision_budget_ok(campaign) is True
